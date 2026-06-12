import logging

from ..domain.enums import CampaignStatus, EmailStatus, StepStatus
from ..domain.preprocessing import PreprocessOptions, preprocess_email_html, strip_html
from ..domain.scheduling import compute_jittered_delay
from ..domain.state_machine import is_valid_transition
from ..domain.templating import resolve_for_recipient
from .ports import (
    AttachmentRepository,
    CampaignRepository,
    CampaignSenderRepository,
    CredentialCipher,
    EmailJobRepository,
    EmailQueue,
    FileStorage,
    Mailer,
    RecipientStateRepository,
    SenderRepository,
    SequenceStepRepository,
)
from .throttling import ThrottleEngine, utc_now

logger = logging.getLogger(__name__)

ALL_SENDERS_EXHAUSTED = "ALL_SENDERS_EXHAUSTED"
REASSIGNMENT_DELAY_MS = 1000
DEFAULT_RETRY_MS = 60_000


class CampaignCompletionChecker:
    def __init__(self, campaign_repo: CampaignRepository, email_job_repo: EmailJobRepository):
        self._campaign_repo = campaign_repo
        self._email_job_repo = email_job_repo

    async def check(self, campaign_id: str) -> None:
        try:
            campaign = await self._campaign_repo.get(campaign_id)
            if campaign is None or campaign.status in (
                CampaignStatus.PAUSED,
                CampaignStatus.CANCELLED,
                CampaignStatus.COMPLETED,
            ):
                return
            if await self._campaign_repo.has_sequence_steps(campaign_id):
                return
            if await self._email_job_repo.non_terminal_count(campaign_id) == 0:
                if is_valid_transition(campaign.status, CampaignStatus.COMPLETED):
                    await self._campaign_repo.update_status_if(
                        campaign_id, campaign.status, CampaignStatus.COMPLETED
                    )
        except Exception:
            logger.exception("Campaign completion check failed for %s", campaign_id)


class SendEmailUseCase:
    def __init__(
        self,
        email_job_repo: EmailJobRepository,
        campaign_repo: CampaignRepository,
        campaign_sender_repo: CampaignSenderRepository,
        sender_repo: SenderRepository,
        sequence_step_repo: SequenceStepRepository,
        recipient_state_repo: RecipientStateRepository,
        attachment_repo: AttachmentRepository,
        throttle: ThrottleEngine,
        mailer: Mailer,
        cipher: CredentialCipher,
        storage: FileStorage,
        queue: EmailQueue,
        completion: CampaignCompletionChecker,
        tracking_base_url: str = "",
    ):
        self._email_job_repo = email_job_repo
        self._campaign_repo = campaign_repo
        self._campaign_sender_repo = campaign_sender_repo
        self._sender_repo = sender_repo
        self._sequence_step_repo = sequence_step_repo
        self._recipient_state_repo = recipient_state_repo
        self._attachment_repo = attachment_repo
        self._throttle = throttle
        self._mailer = mailer
        self._cipher = cipher
        self._storage = storage
        self._queue = queue
        self._completion = completion
        self._tracking_base_url = tracking_base_url

    async def _mark_failed(self, job_id: str, error: str) -> None:
        try:
            job = await self._email_job_repo.get(job_id)
            if job is None:
                return
            job.status = EmailStatus.FAILED
            job.error = error
            job.updated_at = utc_now()
            await self._email_job_repo.update(job)
        except Exception:
            logger.exception("Failed to mark job %s as FAILED", job_id)

    async def _update_step_status(
        self, campaign_id: str, to_email: str, step_id: str, status: StepStatus, error: str | None
    ) -> None:
        try:
            step = await self._sequence_step_repo.get(step_id)
            state = await self._recipient_state_repo.get_by_recipient(campaign_id, to_email)
            if step is None or state is None:
                return
            for entry in state.step_statuses:
                if entry.get("stepNumber") == step.step_number:
                    entry["status"] = status
                    if status == StepStatus.SENT:
                        entry["sentAt"] = utc_now().isoformat()
                    if error:
                        entry["error"] = error
                    break
            await self._recipient_state_repo.update(state)
        except Exception:
            logger.exception("Failed to update sequence step status for %s", to_email)

    async def _release_to_pending(self, job_id: str) -> None:
        await self._email_job_repo.set_status_if(
            job_id, EmailStatus.SENDING, EmailStatus.PENDING
        )

    async def execute(self, email_job_id: str) -> None:
        job = await self._email_job_repo.get(email_job_id)
        if job is None:
            logger.warning("Email job %s not found, skipping", email_job_id)
            return
        campaign = await self._campaign_repo.get(job.campaign_id)
        if campaign is None:
            logger.warning("Campaign %s not found, skipping", job.campaign_id)
            return
        if campaign.status in (CampaignStatus.PAUSED, CampaignStatus.CANCELLED):
            return
        if job.status == EmailStatus.SENT:
            await self._completion.check(campaign.id)
            return
        if job.status != EmailStatus.PENDING:
            return
        if not await self._email_job_repo.claim_pending(email_job_id):
            return
        if campaign.status == CampaignStatus.SCHEDULED:
            await self._campaign_repo.update_status_if(
                campaign.id, CampaignStatus.SCHEDULED, CampaignStatus.SENDING
            )

        sender_id = job.sender_id or campaign.sender_id
        sender = await self._sender_repo.get(sender_id) if sender_id else None
        if sender is None:
            await self._mark_failed(email_job_id, "Sender not found")
            return
        if not sender.is_verified:
            await self._mark_failed(email_job_id, "Sender not verified for SMTP")
            return

        limits = await self._throttle.effective_limits(sender.id)
        if not await self._throttle.has_daily_capacity(sender.id, limits.per_day):
            pool = await self._campaign_sender_repo.list_for_campaign(campaign.id)
            await self._release_to_pending(email_job_id)
            if pool:
                available = await self._throttle.find_available_sender(campaign.id)
                if available:
                    refreshed = await self._email_job_repo.get(email_job_id)
                    if refreshed:
                        refreshed.sender_id = available
                        refreshed.updated_at = utc_now()
                        await self._email_job_repo.update(refreshed)
                    await self._queue.enqueue_send(email_job_id, REASSIGNMENT_DELAY_MS)
                else:
                    await self._campaign_repo.update_status_if(
                        campaign.id,
                        campaign.status,
                        CampaignStatus.PAUSED,
                        pause_reason=ALL_SENDERS_EXHAUSTED,
                    )
            else:
                logger.warning(
                    "Sender %s exhausted with no pool for campaign %s", sender.id, campaign.id
                )
            return

        decision = await self._throttle.can_send(sender.id, campaign.hourly_limit)
        if not decision.allowed:
            await self._release_to_pending(email_job_id)
            base_ms = decision.retry_after_ms or DEFAULT_RETRY_MS
            delay_ms = max(0, round(compute_jittered_delay(base_ms / 1000) * 1000))
            await self._queue.enqueue_send(email_job_id, delay_ms)
            logger.info("Throttled job %s (%s), retrying in %sms", email_job_id, decision.reason, delay_ms)
            return

        try:
            password = self._cipher.decrypt(sender.app_password)
        except Exception:
            await self._mark_failed(email_job_id, "Failed to decrypt sender credentials")
            return

        attachments: list[tuple[str, bytes, str]] = []
        for attachment in await self._attachment_repo.list_for_campaign(campaign.id):
            try:
                content = await self._storage.read(attachment.url)
            except Exception:
                await self._mark_failed(
                    email_job_id, f"Failed to download attachment {attachment.filename}"
                )
                return
            attachments.append((attachment.filename, content, attachment.mime_type))

        raw_subject, raw_body = campaign.subject, campaign.body
        if job.sequence_step_id:
            step = await self._sequence_step_repo.get(job.sequence_step_id)
            if step:
                raw_subject, raw_body = step.subject, step.body
        subject, body = resolve_for_recipient(raw_subject, raw_body, job.column_data or {})

        html_body = body
        if self._tracking_base_url:
            html_body = preprocess_email_html(
                body,
                PreprocessOptions(
                    email_job_id=email_job_id,
                    tracking_base_url=self._tracking_base_url,
                    track_opens=campaign.track_opens,
                    track_clicks=campaign.track_clicks,
                ),
            )
        else:
            logger.warning("TRACKING_BASE_URL not set; sending without tracking")

        try:
            await self._mailer.send(
                host=sender.smtp_host,
                port=sender.smtp_port,
                username=sender.email,
                password=password,
                from_name=sender.name,
                from_email=sender.email,
                to_email=job.to_email,
                subject=subject,
                text_body=strip_html(body),
                html_body=html_body,
                attachments=attachments,
            )
        except Exception as smtp_error:
            message = str(smtp_error) or "SMTP send failed"
            await self._throttle.record_send_result(sender.id, success=False)
            await self._mark_failed(email_job_id, message)
            if job.sequence_step_id:
                await self._update_step_status(
                    campaign.id, job.to_email, job.sequence_step_id, StepStatus.FAILED, message
                )
            await self._completion.check(campaign.id)
            return

        refreshed = await self._email_job_repo.get(email_job_id)
        if refreshed and refreshed.status != EmailStatus.CANCELLED:
            refreshed.status = EmailStatus.SENT
            refreshed.sent_at = utc_now()
            refreshed.updated_at = refreshed.sent_at
            await self._email_job_repo.update(refreshed)
        await self._throttle.record_send_result(sender.id, success=True)
        if job.sequence_step_id:
            await self._update_step_status(
                campaign.id, job.to_email, job.sequence_step_id, StepStatus.SENT, None
            )
        await self._completion.check(campaign.id)
