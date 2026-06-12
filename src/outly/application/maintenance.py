import logging
import uuid
from datetime import datetime, timedelta, timezone

from ..domain.entities import EmailJob
from ..domain.enums import CampaignStatus, EmailStatus, StepStatus
from ..domain.scheduling import compute_jittered_delay
from .ports import (
    CampaignRepository,
    CampaignSenderRepository,
    EmailJobRepository,
    EmailQueue,
    RateLimitRepository,
    RecipientStateRepository,
    RefreshTokenRepository,
    SequenceStepRepository,
)
from .throttling import ThrottleEngine, utc_now

logger = logging.getLogger(__name__)

RATE_COUNTER_RETENTION = timedelta(hours=2)
REVOKED_TOKEN_RETENTION = timedelta(days=7)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class SequenceSchedulerUseCase:
    def __init__(
        self,
        campaign_repo: CampaignRepository,
        sequence_step_repo: SequenceStepRepository,
        recipient_state_repo: RecipientStateRepository,
        email_job_repo: EmailJobRepository,
        campaign_sender_repo: CampaignSenderRepository,
        queue: EmailQueue,
    ):
        self._campaign_repo = campaign_repo
        self._sequence_step_repo = sequence_step_repo
        self._recipient_state_repo = recipient_state_repo
        self._email_job_repo = email_job_repo
        self._campaign_sender_repo = campaign_sender_repo
        self._queue = queue

    async def run(self) -> None:
        campaigns = await self._campaign_repo.list_sequence_campaigns(
            [CampaignStatus.SCHEDULED, CampaignStatus.SENDING]
        )
        for campaign in campaigns:
            steps = await self._sequence_step_repo.list_for_campaign(campaign.id)
            steps.sort(key=lambda step: step.step_number)
            pool = await self._campaign_sender_repo.list_for_campaign(campaign.id)
            pool.sort(key=lambda cs: cs.rotation_order)
            recipients = await self._recipient_state_repo.list_active(campaign.id)
            for recipient in recipients:
                try:
                    await self._advance_recipient(campaign, steps, pool, recipient)
                except Exception:
                    logger.exception(
                        "Scheduler failed for recipient %s in campaign %s",
                        recipient.recipient_email,
                        campaign.id,
                    )
            try:
                active = await self._recipient_state_repo.active_count(campaign.id)
                non_terminal = await self._email_job_repo.non_terminal_count(campaign.id)
                if active == 0 and non_terminal == 0:
                    await self._campaign_repo.update_status_if(
                        campaign.id, campaign.status, CampaignStatus.COMPLETED
                    )
            except Exception:
                logger.exception("Scheduler completion check failed for %s", campaign.id)

    async def _advance_recipient(self, campaign, steps, pool, recipient) -> None:
        statuses = recipient.step_statuses or []
        current = next(
            (s for s in statuses if s.get("stepNumber") == recipient.current_step), None
        )
        if not current or current.get("status") != StepStatus.SENT:
            return

        next_step_number = recipient.current_step + 1
        if next_step_number >= len(steps):
            recipient.completed = True
            recipient.updated_at = utc_now()
            await self._recipient_state_repo.update(recipient)
            return

        next_step = steps[next_step_number]
        sent_at_raw = current.get("sentAt")
        if not sent_at_raw:
            return
        sent_at = _as_utc(datetime.fromisoformat(sent_at_raw.replace("Z", "+00:00")))
        due_at = sent_at + timedelta(days=next_step.wait_days)
        now = utc_now()
        if now < due_at:
            return

        if not await self._recipient_state_repo.advance_step(
            recipient.id, recipient.current_step, next_step_number
        ):
            return

        for entry in statuses:
            if entry.get("stepNumber") == next_step_number:
                entry["status"] = StepStatus.SCHEDULED
        recipient.current_step = next_step_number
        recipient.step_statuses = statuses
        await self._recipient_state_repo.update(recipient)

        if pool:
            assigned_sender_id = pool[next_step_number % len(pool)].sender_id
        else:
            assigned_sender_id = campaign.sender_id

        original = await self._email_job_repo.first_with_column_data(
            campaign.id, recipient.recipient_email
        )
        job = EmailJob(
            id=uuid.uuid4().hex,
            campaign_id=campaign.id,
            sender_id=assigned_sender_id,
            to_email=recipient.recipient_email,
            scheduled_at=now,
            sent_at=None,
            status=EmailStatus.PENDING,
            error=None,
            is_starred=False,
            column_data=original.column_data if original else None,
            is_replied=False,
            sequence_step_id=next_step.id,
            created_at=now,
            updated_at=now,
        )
        await self._email_job_repo.create_many([job])

        for entry in statuses:
            if entry.get("stepNumber") == next_step_number:
                entry["emailJobId"] = job.id
        recipient.step_statuses = statuses
        await self._recipient_state_repo.update(recipient)
        await self._queue.enqueue_send(job.id)


class AutoResumeUseCase:
    def __init__(
        self,
        campaign_repo: CampaignRepository,
        email_job_repo: EmailJobRepository,
        rate_limit_repo: RateLimitRepository,
        refresh_token_repo: RefreshTokenRepository,
        throttle: ThrottleEngine,
        queue: EmailQueue,
    ):
        self._campaign_repo = campaign_repo
        self._email_job_repo = email_job_repo
        self._rate_limit_repo = rate_limit_repo
        self._refresh_token_repo = refresh_token_repo
        self._throttle = throttle
        self._queue = queue

    async def run(self) -> None:
        now = utc_now()
        campaigns = await self._campaign_repo.list_paused_exhausted()
        for campaign in campaigns:
            try:
                earliest = await self._throttle.earliest_resume_time(campaign.id)
                if earliest > now:
                    continue
                resumed = await self._campaign_repo.update_status_if(
                    campaign.id,
                    CampaignStatus.PAUSED,
                    CampaignStatus.SENDING,
                    pause_reason=None,
                )
                if not resumed:
                    continue
                pending = await self._email_job_repo.list_pending_for_campaign(campaign.id)
                for job in pending:
                    delay_ms = round(compute_jittered_delay(1) * 1000)
                    await self._queue.enqueue_send(job.id, delay_ms)
                logger.info("Auto-resumed campaign %s with %d jobs", campaign.id, len(pending))
            except Exception:
                logger.exception("Auto-resume failed for campaign %s", campaign.id)

        deleted_counters = await self._rate_limit_repo.delete_older_than(
            now - RATE_COUNTER_RETENTION
        )
        deleted_tokens = await self._refresh_token_repo.delete_stale(
            now - REVOKED_TOKEN_RETENTION, now
        )
        if deleted_counters or deleted_tokens:
            logger.info(
                "Cleanup removed %d rate counters, %d refresh tokens",
                deleted_counters,
                deleted_tokens,
            )


class SweepUseCase:
    def __init__(
        self,
        campaign_repo: CampaignRepository,
        email_job_repo: EmailJobRepository,
        recipient_state_repo: RecipientStateRepository,
        queue: EmailQueue,
        stale_threshold_ms: int = 300_000,
    ):
        self._campaign_repo = campaign_repo
        self._email_job_repo = email_job_repo
        self._recipient_state_repo = recipient_state_repo
        self._queue = queue
        self._stale_threshold_ms = stale_threshold_ms

    async def recover_orphaned_jobs(self) -> int:
        jobs = await self._email_job_repo.list_by_status(EmailStatus.SENDING)
        recovered = 0
        for job in jobs:
            if await self._email_job_repo.set_status_if(
                job.id, EmailStatus.SENDING, EmailStatus.PENDING
            ):
                await self._queue.enqueue_send(job.id)
                recovered += 1
        if recovered:
            logger.info("Recovered %d orphaned SENDING jobs", recovered)
        return recovered

    async def sweep_stale_sending_jobs(self) -> int:
        cutoff = utc_now() - timedelta(milliseconds=self._stale_threshold_ms)
        jobs = await self._email_job_repo.list_stale_sending(cutoff)
        recovered = 0
        for job in jobs:
            if await self._email_job_repo.set_status_if(
                job.id, EmailStatus.SENDING, EmailStatus.PENDING
            ):
                await self._queue.enqueue_send(job.id)
                recovered += 1
        if recovered:
            logger.info("Recovered %d stale SENDING jobs", recovered)
        return recovered

    async def sweep_stuck_campaigns(self) -> int:
        campaigns = await self._campaign_repo.list_by_status(CampaignStatus.SENDING)
        completed = 0
        for campaign in campaigns:
            try:
                if await self._email_job_repo.non_terminal_count(campaign.id) > 0:
                    continue
                if await self._campaign_repo.has_sequence_steps(campaign.id):
                    if await self._recipient_state_repo.active_count(campaign.id) > 0:
                        continue
                if await self._campaign_repo.update_status_if(
                    campaign.id, CampaignStatus.SENDING, CampaignStatus.COMPLETED
                ):
                    completed += 1
                    logger.info("Marked stuck campaign %s as COMPLETED", campaign.id)
            except Exception:
                logger.exception("Stuck campaign sweep failed for %s", campaign.id)
        return completed
