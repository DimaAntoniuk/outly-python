import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..domain.entities import (
    Attachment,
    CampaignSender,
    EmailCampaign,
    EmailJob,
    RecipientSequenceState,
    SequenceStep,
)
from ..domain.enums import CampaignStatus, EmailStatus, StepStatus
from ..domain.rotation import PoolSender, assign_senders_round_robin
from ..domain.scheduling import schedule_times
from ..domain.state_machine import is_valid_transition
from ..domain.validation import (
    parse_iso_date,
    validate_date_range,
    validate_search_query,
    validate_sequence_steps,
    validate_status_param,
)
from .errors import BadRequest, Conflict, Forbidden, NotFound
from .ports import (
    AttachmentRepository,
    CampaignRepository,
    CampaignSenderRepository,
    EmailJobRepository,
    RateLimitRepository,
    RecipientStateRepository,
    SenderRepository,
    SequenceStepRepository,
)
from .throttling import AdaptiveThrottle, ThrottleEngine, current_hour_window, utc_now

MAX_TOTAL_ATTACHMENT_SIZE = 25 * 1024 * 1024


@dataclass
class CampaignCreateResult:
    campaign_id: str
    sender_pool: list[str]
    enqueue_plan: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class CampaignResumeResult:
    campaign: EmailCampaign
    enqueue_plan: list[tuple[str, int]] = field(default_factory=list)


def _normalized_recipients(emails: list[Any]) -> list[tuple[str, dict[str, str] | None]]:
    seen: dict[str, dict[str, str] | None] = {}
    for entry in emails:
        if isinstance(entry, str):
            address, column_data = entry, None
        elif isinstance(entry, dict) and isinstance(entry.get("email"), str):
            address, column_data = entry["email"], entry.get("columnData")
        else:
            continue
        address = address.strip().lower()
        if address and address not in seen:
            seen[address] = column_data
    return list(seen.items())


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class CampaignService:
    def __init__(
        self,
        campaign_repo: CampaignRepository,
        campaign_sender_repo: CampaignSenderRepository,
        email_job_repo: EmailJobRepository,
        attachment_repo: AttachmentRepository,
        sequence_step_repo: SequenceStepRepository,
        recipient_state_repo: RecipientStateRepository,
        sender_repo: SenderRepository,
        rate_limit_repo: RateLimitRepository,
        throttle: ThrottleEngine,
        adaptive: AdaptiveThrottle,
    ):
        self._campaign_repo = campaign_repo
        self._campaign_sender_repo = campaign_sender_repo
        self._email_job_repo = email_job_repo
        self._attachment_repo = attachment_repo
        self._sequence_step_repo = sequence_step_repo
        self._recipient_state_repo = recipient_state_repo
        self._sender_repo = sender_repo
        self._rate_limit_repo = rate_limit_repo
        self._throttle = throttle
        self._adaptive = adaptive

    async def create(self, user_id: str, payload: dict[str, Any]) -> CampaignCreateResult:
        sender_ids = payload.get("senderIds") or (
            [payload["senderId"]] if payload.get("senderId") else []
        )
        if not isinstance(sender_ids, list) or not sender_ids:
            raise BadRequest("At least one sender is required")

        subject = payload.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            raise BadRequest("Subject must be a non-empty string")
        body = payload.get("body")
        if not isinstance(body, str) or not body.strip():
            raise BadRequest("Body must be a non-empty string")
        start_time = parse_iso_date(payload.get("startTime"))
        if start_time is None:
            raise BadRequest("startTime must be a valid date")
        start_time = _as_utc(start_time)
        delay_seconds = payload.get("delaySeconds")
        if not isinstance(delay_seconds, (int, float)) or isinstance(delay_seconds, bool) or delay_seconds < 0:
            raise BadRequest("delaySeconds must be a number >= 0")
        hourly_limit = payload.get("hourlyLimit")
        if not isinstance(hourly_limit, (int, float)) or isinstance(hourly_limit, bool) or hourly_limit <= 0:
            raise BadRequest("hourlyLimit must be a number > 0")
        emails = payload.get("emails")
        if not isinstance(emails, list) or not emails:
            raise BadRequest("At least one recipient email is required")
        recipients = _normalized_recipients(emails)
        if not recipients:
            raise BadRequest("At least one recipient email is required")

        attachments = payload.get("attachments") or []
        total_size = sum(item.get("size", 0) for item in attachments if isinstance(item, dict))
        if total_size > MAX_TOTAL_ATTACHMENT_SIZE:
            raise BadRequest("Total attachment size exceeds the 25 MB limit")

        steps = payload.get("steps")
        if steps is not None:
            result = validate_sequence_steps(steps)
            if not result.valid:
                raise BadRequest(result.message or "Invalid sequence steps")

        senders = await self._sender_repo.list_by_ids_for_user(sender_ids, user_id)
        if len(senders) != len(set(sender_ids)):
            raise Forbidden("Sender not found or not owned by you")
        if any(not sender.is_verified for sender in senders):
            raise BadRequest("All senders must be verified")

        now = utc_now()
        campaign = EmailCampaign(
            id=uuid.uuid4().hex,
            user_id=user_id,
            sender_id=sender_ids[0],
            subject=subject,
            body=body,
            start_time=start_time,
            delay_seconds=int(delay_seconds),
            hourly_limit=int(hourly_limit),
            total_recipients=len(recipients),
            status=CampaignStatus.SCHEDULED,
            pause_reason=None,
            track_opens=payload.get("trackOpens", True) is not False,
            track_clicks=payload.get("trackClicks", True) is not False,
            created_at=now,
        )
        campaign = await self._campaign_repo.create(campaign)

        sender_by_id = {sender.id: sender for sender in senders}
        ordered_sender_ids = [sid for sid in sender_ids if sid in sender_by_id]
        await self._campaign_sender_repo.create_many(
            [
                CampaignSender(uuid.uuid4().hex, campaign.id, sender_id, order)
                for order, sender_id in enumerate(ordered_sender_ids)
            ]
        )

        pool = [
            PoolSender(sender_id, order, sender_by_id[sender_id].daily_limit)
            for order, sender_id in enumerate(ordered_sender_ids)
        ]
        assignments = assign_senders_round_robin(pool, len(recipients))
        times = schedule_times(
            start_time, len(recipients), int(delay_seconds), int(hourly_limit), len(pool)
        )

        sequence_steps: list[SequenceStep] = []
        if steps:
            sequence_steps.append(
                SequenceStep(uuid.uuid4().hex, campaign.id, 0, subject, body, 0)
            )
            for index, step in enumerate(steps):
                sequence_steps.append(
                    SequenceStep(
                        uuid.uuid4().hex,
                        campaign.id,
                        index + 1,
                        step["subject"],
                        step["body"],
                        step["waitDays"],
                    )
                )
            await self._sequence_step_repo.create_many(sequence_steps)

        first_step_id = sequence_steps[0].id if sequence_steps else None
        jobs = [
            EmailJob(
                id=uuid.uuid4().hex,
                campaign_id=campaign.id,
                sender_id=assignments[index] if index < len(assignments) else None,
                to_email=address,
                scheduled_at=times[index],
                sent_at=None,
                status=EmailStatus.PENDING,
                error=None,
                is_starred=False,
                column_data=column_data,
                is_replied=False,
                sequence_step_id=first_step_id,
                created_at=now,
                updated_at=now,
            )
            for index, (address, column_data) in enumerate(recipients)
        ]
        await self._email_job_repo.create_many(jobs)

        if attachments:
            await self._attachment_repo.create_many(
                [
                    Attachment(
                        id=uuid.uuid4().hex,
                        campaign_id=campaign.id,
                        url=item["url"],
                        filename=item["filename"],
                        size=item["size"],
                        mime_type=item["mimeType"],
                    )
                    for item in attachments
                ]
            )

        if sequence_steps:
            job_by_email = {job.to_email: job for job in jobs}
            states = []
            for address, _ in recipients:
                statuses: list[dict[str, Any]] = [
                    {
                        "stepNumber": 0,
                        "status": StepStatus.SCHEDULED,
                        "emailJobId": job_by_email[address].id,
                    }
                ]
                statuses.extend(
                    {"stepNumber": step.step_number, "status": StepStatus.PENDING}
                    for step in sequence_steps[1:]
                )
                states.append(
                    RecipientSequenceState(
                        id=uuid.uuid4().hex,
                        campaign_id=campaign.id,
                        recipient_email=address,
                        current_step=0,
                        paused=False,
                        replied=False,
                        completed=False,
                        step_statuses=statuses,
                    )
                )
            await self._recipient_state_repo.create_many(states)

        enqueue_plan = [
            (job.id, max(0, int((job.scheduled_at - now).total_seconds() * 1000)))
            for job in jobs
        ]
        return CampaignCreateResult(campaign.id, ordered_sender_ids, enqueue_plan)

    async def list_for_user(self, user_id: str) -> list[EmailCampaign]:
        return await self._campaign_repo.list_for_user(user_id)

    async def list_completed(self, user_id: str) -> list[EmailCampaign]:
        return await self._campaign_repo.list_for_user(user_id, CampaignStatus.COMPLETED)

    async def search(self, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("q")
        for check in (
            validate_search_query(query),
            validate_status_param(params.get("status"), tuple(CampaignStatus)),
            validate_date_range(params.get("dateFrom"), params.get("dateTo")),
        ):
            if not check.valid:
                raise BadRequest(check.message or "Invalid search parameters")
        status = CampaignStatus(params["status"]) if params.get("status") else None
        results = await self._campaign_repo.search(
            user_id,
            query,
            status,
            params.get("senderId"),
            parse_iso_date(params.get("dateFrom")),
            parse_iso_date(params.get("dateTo")),
        )
        return {"results": results, "total": len(results), "filters": params}

    async def _get_owned(self, user_id: str, campaign_id: str) -> EmailCampaign:
        campaign = await self._campaign_repo.get(campaign_id)
        if campaign is None:
            raise NotFound("Campaign not found")
        if campaign.user_id != user_id:
            raise Forbidden("Forbidden")
        return campaign

    async def get_detail(self, user_id: str, campaign_id: str) -> dict[str, Any]:
        campaign = await self._get_owned(user_id, campaign_id)
        counts = await self._email_job_repo.status_counts(campaign_id)
        pool = await self._campaign_sender_repo.list_for_campaign(campaign_id)

        sender_stats = []
        effective_send_rate = 0
        throttle_reasons: set[str] = set()
        for campaign_sender in pool:
            sender = await self._sender_repo.get(campaign_sender.sender_id)
            if sender is None:
                continue
            limits = await self._throttle.effective_limits(sender.id)
            effective_send_rate += limits.per_minute
            if limits.is_throttled:
                throttle_reasons.add("error-throttled")
            if limits.is_warmup:
                throttle_reasons.add("warmup")
            if limits.is_cooldown:
                throttle_reasons.add("cooldown")
            sender_stats.append(
                {
                    "senderId": sender.id,
                    "email": sender.email,
                    "name": sender.name,
                    "rotationOrder": campaign_sender.rotation_order,
                    "dailySent": await self._throttle.sent_count_today(sender.id),
                    "effectiveLimits": {
                        "perMinute": limits.per_minute,
                        "perHour": limits.per_hour,
                        "perDay": limits.per_day,
                    },
                }
            )

        active = counts.get("PENDING", 0) + counts.get("SENDING", 0)
        estimated_minutes = (
            math.ceil(active / effective_send_rate) if effective_send_rate > 0 else None
        )
        return {
            "campaign": campaign,
            "senderPool": [cs.sender_id for cs in pool],
            "senderStats": sender_stats,
            "_count": counts,
            "effectiveSendRate": effective_send_rate,
            "activeThrottleReasons": sorted(throttle_reasons),
            "estimatedCompletionTime": estimated_minutes,
        }

    async def throttle_status(self, user_id: str, campaign_id: str) -> dict[str, Any]:
        await self._get_owned(user_id, campaign_id)
        pool = await self._campaign_sender_repo.list_for_campaign(campaign_id)
        now = utc_now()
        senders = []
        for campaign_sender in pool:
            sender = await self._sender_repo.get(campaign_sender.sender_id)
            if sender is None:
                continue
            limits = await self._throttle.effective_limits(sender.id)
            adaptive = await self._adaptive.get_state(sender.id)
            warmup_active = limits.is_warmup
            schedule_opted_out = False
            warmup_status = "active" if warmup_active else "inactive"
            senders.append(
                {
                    "senderId": sender.id,
                    "email": sender.email,
                    "name": sender.name,
                    "currentHourlyCount": await self._rate_limit_repo.hour_count(
                        sender.id, current_hour_window(now)
                    ),
                    "currentDailyCount": await self._throttle.sent_count_today(sender.id),
                    "effectiveLimits": {
                        "perMinute": limits.per_minute,
                        "perHour": limits.per_hour,
                        "perDay": limits.per_day,
                    },
                    "warmupStatus": warmup_status if not schedule_opted_out else "opted-out",
                    "cooldownState": {
                        "status": "active" if adaptive.is_cooldown else "inactive",
                        "expiresAt": adaptive.cooldown_expires_at,
                        "rateMultiplier": adaptive.rate_multiplier,
                    },
                }
            )
        return {"campaignId": campaign_id, "senders": senders}

    async def pause(self, user_id: str, campaign_id: str) -> EmailCampaign:
        campaign = await self._get_owned(user_id, campaign_id)
        if not is_valid_transition(campaign.status, CampaignStatus.PAUSED):
            raise Conflict(f"Cannot pause a campaign in {campaign.status} state")
        updated = await self._campaign_repo.update_status_if(
            campaign_id, campaign.status, CampaignStatus.PAUSED
        )
        if not updated:
            raise Conflict("Campaign state has changed, please retry")
        campaign.status = CampaignStatus.PAUSED
        return campaign

    async def resume(self, user_id: str, campaign_id: str) -> CampaignResumeResult:
        campaign = await self._get_owned(user_id, campaign_id)
        if campaign.status != CampaignStatus.PAUSED:
            raise Conflict("Only paused campaigns can be resumed")

        jobs = await self._email_job_repo.list_for_campaign(campaign_id)
        pending = [job for job in jobs if job.status == EmailStatus.PENDING]
        sending = [job for job in jobs if job.status == EmailStatus.SENDING]
        now = utc_now()

        if not pending and not sending:
            await self._campaign_repo.update_status_if(
                campaign_id, CampaignStatus.PAUSED, CampaignStatus.COMPLETED
            )
            campaign.status = CampaignStatus.COMPLETED
            return CampaignResumeResult(campaign)

        enqueue_plan: list[tuple[str, int]] = []
        overdue = sorted(
            (job for job in pending if _as_utc(job.scheduled_at) <= now),
            key=lambda job: job.scheduled_at,
        )
        for index, job in enumerate(overdue):
            job.scheduled_at = now + timedelta(seconds=index * campaign.delay_seconds)
            job.updated_at = now
            await self._email_job_repo.update(job)
            enqueue_plan.append((job.id, index * campaign.delay_seconds * 1000))

        updated = await self._campaign_repo.update_status_if(
            campaign_id, CampaignStatus.PAUSED, CampaignStatus.SENDING
        )
        if not updated:
            raise Conflict("Campaign state has changed, please retry")
        campaign.status = CampaignStatus.SENDING
        campaign.pause_reason = None
        return CampaignResumeResult(campaign, enqueue_plan)

    async def cancel(self, user_id: str, campaign_id: str) -> EmailCampaign:
        campaign = await self._get_owned(user_id, campaign_id)
        if not is_valid_transition(campaign.status, CampaignStatus.CANCELLED):
            raise Conflict(f"Cannot cancel a campaign in {campaign.status} state")
        updated = await self._campaign_repo.update_status_if(
            campaign_id, campaign.status, CampaignStatus.CANCELLED
        )
        if not updated:
            raise Conflict("Campaign state has changed, please retry")
        await self._email_job_repo.cancel_pending(campaign_id)
        campaign.status = CampaignStatus.CANCELLED
        return campaign
