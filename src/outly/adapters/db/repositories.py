from dataclasses import fields
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from sqlalchemy import Text, cast, delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.entities import (
    Attachment,
    CampaignSender,
    EmailCampaign,
    EmailJob,
    EmailTemplate,
    ProviderProfile,
    RecipientSequenceState,
    RefreshToken,
    Sender,
    SenderCooldown,
    SequenceStep,
    TrackingEvent,
    User,
    WarmupSchedule,
)
from ...domain.enums import CampaignStatus, EmailStatus, TrackingEventType
from ...domain.state_machine import TERMINAL_EMAIL_STATUSES
from .models import (
    AttachmentRow,
    CampaignSenderRow,
    EmailCampaignRow,
    EmailJobRow,
    EmailTemplateRow,
    ProviderProfileRow,
    RateLimitCounterRow,
    RecipientSequenceStateRow,
    RefreshTokenRow,
    SenderCooldownRow,
    SenderRow,
    SequenceStepRow,
    TrackingEventRow,
    UserRow,
    WarmupScheduleRow,
    new_id,
)

T = TypeVar("T")

TERMINAL_STATUSES = [str(status) for status in TERMINAL_EMAIL_STATUSES]
ALL_SENDERS_EXHAUSTED = "ALL_SENDERS_EXHAUSTED"


def _aware(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def to_entity(row: Any, entity_cls: type[T]) -> T:
    return entity_cls(**{f.name: _aware(getattr(row, f.name)) for f in fields(entity_cls)})  # type: ignore[arg-type]


def apply_entity(row: Any, entity: Any) -> None:
    for f in fields(entity):
        if f.name != "id" and hasattr(row, f.name):
            setattr(row, f.name, getattr(entity, f.name))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SqlUserRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, user_id: str) -> User | None:
        row = await self._session.get(UserRow, user_id)
        return to_entity(row, User) if row else None

    async def upsert_google_user(
        self, google_id: str, email: str, name: str, avatar_url: str | None
    ) -> tuple[User, bool]:
        row = await self._session.scalar(select(UserRow).where(UserRow.google_id == google_id))
        if row:
            row.name = name
            row.avatar_url = avatar_url
            await self._session.flush()
            return to_entity(row, User), False
        now = _now()
        row = UserRow(
            id=new_id(), google_id=google_id, email=email, name=name,
            avatar_url=avatar_url, created_at=now,
        )
        self._session.add(row)
        self._session.add(
            SenderRow(
                id=new_id(), user_id=row.id, email=email, name=name, app_password="",
                is_verified=False, created_at=now, updated_at=now,
            )
        )
        await self._session.flush()
        return to_entity(row, User), True


class SqlRefreshTokenRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, token: str, user_id: str, expires_at: datetime) -> RefreshToken:
        row = RefreshTokenRow(
            id=new_id(), token=token, user_id=user_id, revoked=False,
            expires_at=expires_at, created_at=_now(),
        )
        self._session.add(row)
        await self._session.flush()
        return to_entity(row, RefreshToken)

    async def get_by_token(self, token: str) -> RefreshToken | None:
        row = await self._session.scalar(
            select(RefreshTokenRow).where(RefreshTokenRow.token == token)
        )
        return to_entity(row, RefreshToken) if row else None

    async def revoke(self, token: str) -> None:
        await self._session.execute(
            update(RefreshTokenRow).where(RefreshTokenRow.token == token).values(revoked=True)
        )

    async def delete_stale(self, revoked_before: datetime, now: datetime) -> int:
        result = await self._session.execute(
            delete(RefreshTokenRow).where(
                or_(
                    (RefreshTokenRow.revoked.is_(True))
                    & (RefreshTokenRow.created_at < revoked_before),
                    RefreshTokenRow.expires_at < now,
                )
            )
        )
        return result.rowcount or 0


class SqlSenderRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, sender: Sender) -> Sender:
        row = SenderRow(id=sender.id)
        apply_entity(row, sender)
        self._session.add(row)
        await self._session.flush()
        return to_entity(row, Sender)

    async def get(self, sender_id: str) -> Sender | None:
        row = await self._session.get(SenderRow, sender_id)
        return to_entity(row, Sender) if row else None

    async def get_owned(self, sender_id: str, user_id: str) -> Sender | None:
        row = await self._session.scalar(
            select(SenderRow).where(SenderRow.id == sender_id, SenderRow.user_id == user_id)
        )
        return to_entity(row, Sender) if row else None

    async def list_for_user(self, user_id: str) -> list[Sender]:
        rows = await self._session.scalars(
            select(SenderRow)
            .where(SenderRow.user_id == user_id)
            .order_by(SenderRow.created_at.desc())
        )
        return [to_entity(row, Sender) for row in rows]

    async def list_by_ids_for_user(self, sender_ids: list[str], user_id: str) -> list[Sender]:
        rows = await self._session.scalars(
            select(SenderRow).where(
                SenderRow.id.in_(sender_ids), SenderRow.user_id == user_id
            )
        )
        return [to_entity(row, Sender) for row in rows]

    async def update(self, sender: Sender) -> Sender:
        row = await self._session.get(SenderRow, sender.id)
        if row is None:
            raise ValueError(f"Sender {sender.id} not found")
        sender.updated_at = _now()
        apply_entity(row, sender)
        await self._session.flush()
        return to_entity(row, Sender)


class SqlProviderProfileRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, profile_id: str) -> ProviderProfile | None:
        row = await self._session.get(ProviderProfileRow, profile_id)
        return to_entity(row, ProviderProfile) if row else None

    async def get_by_host(self, smtp_host: str) -> ProviderProfile | None:
        row = await self._session.scalar(
            select(ProviderProfileRow).where(
                ProviderProfileRow.smtp_host_pattern == smtp_host
            )
        )
        return to_entity(row, ProviderProfile) if row else None

    async def get_wildcard(self) -> ProviderProfile | None:
        return await self.get_by_host("*")


class SqlWarmupScheduleRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_sender(self, sender_id: str) -> WarmupSchedule | None:
        row = await self._session.scalar(
            select(WarmupScheduleRow).where(WarmupScheduleRow.sender_id == sender_id)
        )
        return to_entity(row, WarmupSchedule) if row else None

    async def create(self, schedule: WarmupSchedule) -> WarmupSchedule:
        row = WarmupScheduleRow(id=schedule.id)
        apply_entity(row, schedule)
        self._session.add(row)
        await self._session.flush()
        return to_entity(row, WarmupSchedule)


class SqlSenderCooldownRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_sender(self, sender_id: str) -> SenderCooldown | None:
        row = await self._session.scalar(
            select(SenderCooldownRow).where(SenderCooldownRow.sender_id == sender_id)
        )
        return to_entity(row, SenderCooldown) if row else None

    async def upsert(
        self, sender_id: str, consecutive_errors: int, cooldown_until: datetime | None
    ) -> None:
        row = await self._session.scalar(
            select(SenderCooldownRow).where(SenderCooldownRow.sender_id == sender_id)
        )
        now = _now()
        if row:
            row.consecutive_errors = consecutive_errors
            row.cooldown_until = cooldown_until
            row.updated_at = now
        else:
            self._session.add(
                SenderCooldownRow(
                    id=new_id(), sender_id=sender_id, consecutive_errors=consecutive_errors,
                    cooldown_until=cooldown_until, created_at=now, updated_at=now,
                )
            )
        await self._session.flush()


class SqlRateLimitRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def minute_count(self, sender_id: str, minute_window: datetime) -> int:
        count = await self._session.scalar(
            select(func.coalesce(func.sum(RateLimitCounterRow.count), 0)).where(
                RateLimitCounterRow.sender_id == sender_id,
                RateLimitCounterRow.minute_window == minute_window,
            )
        )
        return int(count or 0)

    async def hour_count(self, sender_id: str, hour_window: datetime) -> int:
        count = await self._session.scalar(
            select(func.coalesce(func.sum(RateLimitCounterRow.count), 0)).where(
                RateLimitCounterRow.sender_id == sender_id,
                RateLimitCounterRow.hour_window == hour_window,
            )
        )
        return int(count or 0)

    async def increment(
        self, sender_id: str, minute_window: datetime, hour_window: datetime
    ) -> None:
        row = await self._session.scalar(
            select(RateLimitCounterRow).where(
                RateLimitCounterRow.sender_id == sender_id,
                RateLimitCounterRow.minute_window == minute_window,
            )
        )
        if row:
            row.count += 1
        else:
            self._session.add(
                RateLimitCounterRow(
                    id=new_id(), sender_id=sender_id, minute_window=minute_window,
                    hour_window=hour_window, count=1, created_at=_now(),
                )
            )
        await self._session.flush()

    async def delete_older_than(self, cutoff: datetime) -> int:
        result = await self._session.execute(
            delete(RateLimitCounterRow).where(RateLimitCounterRow.hour_window < cutoff)
        )
        return result.rowcount or 0


class SqlCampaignRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, campaign: EmailCampaign) -> EmailCampaign:
        row = EmailCampaignRow(id=campaign.id)
        apply_entity(row, campaign)
        self._session.add(row)
        await self._session.flush()
        return to_entity(row, EmailCampaign)

    async def get(self, campaign_id: str) -> EmailCampaign | None:
        row = await self._session.get(EmailCampaignRow, campaign_id)
        return to_entity(row, EmailCampaign) if row else None

    async def list_for_user(
        self, user_id: str, status: CampaignStatus | None = None
    ) -> list[EmailCampaign]:
        query = (
            select(EmailCampaignRow)
            .where(EmailCampaignRow.user_id == user_id)
            .order_by(EmailCampaignRow.created_at.desc())
        )
        if status:
            query = query.where(EmailCampaignRow.status == str(status))
        rows = await self._session.scalars(query)
        return [to_entity(row, EmailCampaign) for row in rows]

    async def search(
        self,
        user_id: str,
        query: str | None,
        status: CampaignStatus | None,
        sender_id: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
    ) -> list[EmailCampaign]:
        statement = (
            select(EmailCampaignRow)
            .where(EmailCampaignRow.user_id == user_id)
            .order_by(EmailCampaignRow.created_at.desc())
        )
        if query:
            pattern = f"%{query}%"
            statement = statement.where(
                or_(
                    EmailCampaignRow.subject.ilike(pattern),
                    EmailCampaignRow.body.ilike(pattern),
                )
            )
        if status:
            statement = statement.where(EmailCampaignRow.status == str(status))
        if sender_id:
            statement = statement.where(EmailCampaignRow.sender_id == sender_id)
        if date_from:
            statement = statement.where(EmailCampaignRow.created_at >= date_from)
        if date_to:
            statement = statement.where(EmailCampaignRow.created_at <= date_to)
        rows = await self._session.scalars(statement)
        return [to_entity(row, EmailCampaign) for row in rows]

    async def update_status_if(
        self,
        campaign_id: str,
        expected: CampaignStatus,
        target: CampaignStatus,
        pause_reason: str | None = None,
    ) -> bool:
        result = await self._session.execute(
            update(EmailCampaignRow)
            .where(
                EmailCampaignRow.id == campaign_id,
                EmailCampaignRow.status == str(expected),
            )
            .values(status=str(target), pause_reason=pause_reason)
        )
        return bool(result.rowcount)

    async def list_by_status(self, status: CampaignStatus) -> list[EmailCampaign]:
        rows = await self._session.scalars(
            select(EmailCampaignRow).where(EmailCampaignRow.status == str(status))
        )
        return [to_entity(row, EmailCampaign) for row in rows]

    async def list_paused_exhausted(self) -> list[EmailCampaign]:
        rows = await self._session.scalars(
            select(EmailCampaignRow).where(
                EmailCampaignRow.status == str(CampaignStatus.PAUSED),
                EmailCampaignRow.pause_reason == ALL_SENDERS_EXHAUSTED,
            )
        )
        return [to_entity(row, EmailCampaign) for row in rows]

    async def has_sequence_steps(self, campaign_id: str) -> bool:
        result = await self._session.scalar(
            select(
                exists().where(SequenceStepRow.campaign_id == campaign_id)
            )
        )
        return bool(result)

    async def list_sequence_campaigns(
        self, statuses: list[CampaignStatus]
    ) -> list[EmailCampaign]:
        rows = await self._session.scalars(
            select(EmailCampaignRow).where(
                EmailCampaignRow.status.in_([str(s) for s in statuses]),
                exists().where(SequenceStepRow.campaign_id == EmailCampaignRow.id),
            )
        )
        return [to_entity(row, EmailCampaign) for row in rows]


class SqlCampaignSenderRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_many(self, campaign_senders: list[CampaignSender]) -> None:
        for campaign_sender in campaign_senders:
            row = CampaignSenderRow(id=campaign_sender.id)
            apply_entity(row, campaign_sender)
            self._session.add(row)
        await self._session.flush()

    async def list_for_campaign(self, campaign_id: str) -> list[CampaignSender]:
        rows = await self._session.scalars(
            select(CampaignSenderRow)
            .where(CampaignSenderRow.campaign_id == campaign_id)
            .order_by(CampaignSenderRow.rotation_order)
        )
        return [to_entity(row, CampaignSender) for row in rows]


class SqlEmailJobRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_many(self, jobs: list[EmailJob]) -> None:
        for job in jobs:
            row = EmailJobRow(id=job.id)
            apply_entity(row, job)
            self._session.add(row)
        await self._session.flush()

    async def get(self, job_id: str) -> EmailJob | None:
        row = await self._session.get(EmailJobRow, job_id)
        if row:
            await self._session.refresh(row)
        return to_entity(row, EmailJob) if row else None

    async def get_owned(self, job_id: str, user_id: str) -> EmailJob | None:
        row = await self._session.scalar(
            select(EmailJobRow)
            .join(EmailCampaignRow, EmailJobRow.campaign_id == EmailCampaignRow.id)
            .where(EmailJobRow.id == job_id, EmailCampaignRow.user_id == user_id)
        )
        return to_entity(row, EmailJob) if row else None

    async def update(self, job: EmailJob) -> EmailJob:
        row = await self._session.get(EmailJobRow, job.id)
        if row is None:
            raise ValueError(f"Email job {job.id} not found")
        apply_entity(row, job)
        await self._session.flush()
        return to_entity(row, EmailJob)

    async def claim_pending(self, job_id: str) -> bool:
        return await self.set_status_if(job_id, EmailStatus.PENDING, EmailStatus.SENDING)

    async def set_status_if(
        self, job_id: str, expected: EmailStatus, target: EmailStatus
    ) -> bool:
        result = await self._session.execute(
            update(EmailJobRow)
            .where(EmailJobRow.id == job_id, EmailJobRow.status == str(expected))
            .values(status=str(target), updated_at=_now())
        )
        return bool(result.rowcount)

    async def list_for_campaign(self, campaign_id: str) -> list[EmailJob]:
        rows = await self._session.scalars(
            select(EmailJobRow).where(EmailJobRow.campaign_id == campaign_id)
        )
        return [to_entity(row, EmailJob) for row in rows]

    async def status_counts(self, campaign_id: str) -> dict[str, int]:
        rows = await self._session.execute(
            select(EmailJobRow.status, func.count())
            .where(EmailJobRow.campaign_id == campaign_id)
            .group_by(EmailJobRow.status)
        )
        return {status: count for status, count in rows.all()}

    async def non_terminal_count(self, campaign_id: str) -> int:
        count = await self._session.scalar(
            select(func.count()).where(
                EmailJobRow.campaign_id == campaign_id,
                EmailJobRow.status.notin_(TERMINAL_STATUSES),
            )
        )
        return int(count or 0)

    async def cancel_pending(self, campaign_id: str) -> int:
        result = await self._session.execute(
            update(EmailJobRow)
            .where(
                EmailJobRow.campaign_id == campaign_id,
                EmailJobRow.status == str(EmailStatus.PENDING),
            )
            .values(status=str(EmailStatus.CANCELLED), updated_at=_now())
        )
        return result.rowcount or 0

    async def list_pending_for_campaign(self, campaign_id: str) -> list[EmailJob]:
        rows = await self._session.scalars(
            select(EmailJobRow).where(
                EmailJobRow.campaign_id == campaign_id,
                EmailJobRow.status == str(EmailStatus.PENDING),
            )
        )
        return [to_entity(row, EmailJob) for row in rows]

    async def sent_count_today(self, sender_id: str, now: datetime) -> int:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        count = await self._session.scalar(
            select(func.count()).where(
                EmailJobRow.sender_id == sender_id,
                EmailJobRow.status == str(EmailStatus.SENT),
                EmailJobRow.sent_at >= day_start,
                EmailJobRow.sent_at < day_end,
            )
        )
        return int(count or 0)

    async def recent_outcomes(self, sender_id: str, since: datetime) -> list[EmailJob]:
        rows = await self._session.scalars(
            select(EmailJobRow).where(
                EmailJobRow.sender_id == sender_id,
                EmailJobRow.status.in_(
                    [str(EmailStatus.SENT), str(EmailStatus.FAILED)]
                ),
                EmailJobRow.created_at >= since,
            )
        )
        return [to_entity(row, EmailJob) for row in rows]

    async def list_by_status(self, status: EmailStatus) -> list[EmailJob]:
        rows = await self._session.scalars(
            select(EmailJobRow).where(EmailJobRow.status == str(status))
        )
        return [to_entity(row, EmailJob) for row in rows]

    async def list_stale_sending(self, updated_before: datetime) -> list[EmailJob]:
        rows = await self._session.scalars(
            select(EmailJobRow).where(
                EmailJobRow.status == str(EmailStatus.SENDING),
                EmailJobRow.updated_at < updated_before,
            )
        )
        return [to_entity(row, EmailJob) for row in rows]

    async def list_for_user(
        self,
        user_id: str,
        status: EmailStatus | None,
        order_by: str,
        descending: bool,
        limit: int,
        offset: int,
    ) -> list[EmailJob]:
        column = getattr(EmailJobRow, order_by)
        query = (
            select(EmailJobRow)
            .join(EmailCampaignRow, EmailJobRow.campaign_id == EmailCampaignRow.id)
            .where(EmailCampaignRow.user_id == user_id)
            .order_by(column.desc() if descending else column.asc())
            .limit(limit)
            .offset(offset)
        )
        if status:
            query = query.where(EmailJobRow.status == str(status))
        rows = await self._session.scalars(query)
        return [to_entity(row, EmailJob) for row in rows]

    async def list_for_sender(self, sender_id: str, user_id: str) -> list[EmailJob]:
        rows = await self._session.scalars(
            select(EmailJobRow)
            .join(EmailCampaignRow, EmailJobRow.campaign_id == EmailCampaignRow.id)
            .where(
                EmailJobRow.sender_id == sender_id,
                EmailCampaignRow.user_id == user_id,
            )
            .order_by(EmailJobRow.created_at.desc())
        )
        return [to_entity(row, EmailJob) for row in rows]

    async def search(
        self,
        user_id: str,
        query: str | None,
        status: EmailStatus | None,
        sender_id: str | None,
        date_field: str,
        date_from: datetime | None,
        date_to: datetime | None,
        starred: bool | None,
    ) -> list[EmailJob]:
        column_map = {
            "createdAt": EmailJobRow.created_at,
            "scheduledAt": EmailJobRow.scheduled_at,
            "sentAt": EmailJobRow.sent_at,
        }
        date_column = column_map.get(date_field, EmailJobRow.created_at)
        statement = (
            select(EmailJobRow)
            .join(EmailCampaignRow, EmailJobRow.campaign_id == EmailCampaignRow.id)
            .where(EmailCampaignRow.user_id == user_id)
            .order_by(EmailJobRow.created_at.desc())
        )
        if query:
            pattern = f"%{query}%"
            statement = statement.where(
                or_(
                    EmailJobRow.to_email.ilike(pattern),
                    cast(EmailJobRow.column_data, Text).ilike(pattern),
                    EmailCampaignRow.subject.ilike(pattern),
                    EmailCampaignRow.body.ilike(pattern),
                )
            )
        if status:
            statement = statement.where(EmailJobRow.status == str(status))
        if sender_id:
            statement = statement.where(EmailJobRow.sender_id == sender_id)
        if date_from:
            statement = statement.where(date_column >= date_from)
        if date_to:
            statement = statement.where(date_column <= date_to)
        if starred is not None:
            statement = statement.where(EmailJobRow.is_starred.is_(starred))
        rows = await self._session.scalars(statement)
        return [to_entity(row, EmailJob) for row in rows]

    async def first_with_column_data(
        self, campaign_id: str, to_email: str
    ) -> EmailJob | None:
        row = await self._session.scalar(
            select(EmailJobRow)
            .where(
                EmailJobRow.campaign_id == campaign_id,
                EmailJobRow.to_email == to_email,
                EmailJobRow.column_data.is_not(None),
            )
            .order_by(EmailJobRow.created_at.asc())
            .limit(1)
        )
        return to_entity(row, EmailJob) if row else None


class SqlAttachmentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_many(self, attachments: list[Attachment]) -> None:
        for attachment in attachments:
            row = AttachmentRow(id=attachment.id)
            apply_entity(row, attachment)
            self._session.add(row)
        await self._session.flush()

    async def list_for_campaign(self, campaign_id: str) -> list[Attachment]:
        rows = await self._session.scalars(
            select(AttachmentRow).where(AttachmentRow.campaign_id == campaign_id)
        )
        return [to_entity(row, Attachment) for row in rows]

    async def owner_id_by_url(self, url: str) -> str | None:
        return await self._session.scalar(
            select(EmailCampaignRow.user_id)
            .join(AttachmentRow, AttachmentRow.campaign_id == EmailCampaignRow.id)
            .where(AttachmentRow.url == url)
            .limit(1)
        )


class SqlTemplateRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, template: EmailTemplate) -> EmailTemplate:
        row = EmailTemplateRow(id=template.id)
        apply_entity(row, template)
        self._session.add(row)
        await self._session.flush()
        return to_entity(row, EmailTemplate)

    async def get(self, template_id: str) -> EmailTemplate | None:
        row = await self._session.get(EmailTemplateRow, template_id)
        return to_entity(row, EmailTemplate) if row else None

    async def list_for_user(self, user_id: str) -> list[EmailTemplate]:
        rows = await self._session.scalars(
            select(EmailTemplateRow)
            .where(EmailTemplateRow.user_id == user_id)
            .order_by(EmailTemplateRow.updated_at.desc())
        )
        return [to_entity(row, EmailTemplate) for row in rows]

    async def update(self, template: EmailTemplate) -> EmailTemplate:
        row = await self._session.get(EmailTemplateRow, template.id)
        if row is None:
            raise ValueError(f"Template {template.id} not found")
        apply_entity(row, template)
        await self._session.flush()
        return to_entity(row, EmailTemplate)

    async def delete(self, template_id: str) -> None:
        await self._session.execute(
            delete(EmailTemplateRow).where(EmailTemplateRow.id == template_id)
        )

    async def name_exists(
        self, user_id: str, name: str, exclude_id: str | None = None
    ) -> bool:
        query = select(EmailTemplateRow.id).where(
            EmailTemplateRow.user_id == user_id, EmailTemplateRow.name == name
        )
        if exclude_id:
            query = query.where(EmailTemplateRow.id != exclude_id)
        return await self._session.scalar(query.limit(1)) is not None


class SqlSequenceStepRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_many(self, steps: list[SequenceStep]) -> None:
        for step in steps:
            row = SequenceStepRow(id=step.id)
            apply_entity(row, step)
            self._session.add(row)
        await self._session.flush()

    async def get(self, step_id: str) -> SequenceStep | None:
        row = await self._session.get(SequenceStepRow, step_id)
        return to_entity(row, SequenceStep) if row else None

    async def list_for_campaign(self, campaign_id: str) -> list[SequenceStep]:
        rows = await self._session.scalars(
            select(SequenceStepRow)
            .where(SequenceStepRow.campaign_id == campaign_id)
            .order_by(SequenceStepRow.step_number)
        )
        return [to_entity(row, SequenceStep) for row in rows]


class SqlRecipientStateRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    def _active_filter(self):
        return (
            RecipientSequenceStateRow.completed.is_(False),
            RecipientSequenceStateRow.paused.is_(False),
            RecipientSequenceStateRow.replied.is_(False),
        )

    async def create_many(self, states: list[RecipientSequenceState]) -> None:
        now = _now()
        for state in states:
            row = RecipientSequenceStateRow(id=state.id, created_at=now, updated_at=now)
            state.created_at = state.created_at or now
            state.updated_at = state.updated_at or now
            apply_entity(row, state)
            self._session.add(row)
        await self._session.flush()

    async def get(self, state_id: str) -> RecipientSequenceState | None:
        row = await self._session.get(RecipientSequenceStateRow, state_id)
        return to_entity(row, RecipientSequenceState) if row else None

    async def get_by_recipient(
        self, campaign_id: str, recipient_email: str
    ) -> RecipientSequenceState | None:
        row = await self._session.scalar(
            select(RecipientSequenceStateRow).where(
                RecipientSequenceStateRow.campaign_id == campaign_id,
                RecipientSequenceStateRow.recipient_email == recipient_email,
            )
        )
        return to_entity(row, RecipientSequenceState) if row else None

    async def list_for_campaign(self, campaign_id: str) -> list[RecipientSequenceState]:
        rows = await self._session.scalars(
            select(RecipientSequenceStateRow).where(
                RecipientSequenceStateRow.campaign_id == campaign_id
            )
        )
        return [to_entity(row, RecipientSequenceState) for row in rows]

    async def list_active(self, campaign_id: str) -> list[RecipientSequenceState]:
        rows = await self._session.scalars(
            select(RecipientSequenceStateRow).where(
                RecipientSequenceStateRow.campaign_id == campaign_id,
                *self._active_filter(),
            )
        )
        return [to_entity(row, RecipientSequenceState) for row in rows]

    async def active_count(self, campaign_id: str) -> int:
        count = await self._session.scalar(
            select(func.count()).where(
                RecipientSequenceStateRow.campaign_id == campaign_id,
                *self._active_filter(),
            )
        )
        return int(count or 0)

    async def advance_step(self, state_id: str, expected_step: int, next_step: int) -> bool:
        result = await self._session.execute(
            update(RecipientSequenceStateRow)
            .where(
                RecipientSequenceStateRow.id == state_id,
                RecipientSequenceStateRow.current_step == expected_step,
            )
            .values(current_step=next_step, updated_at=_now())
        )
        return bool(result.rowcount)

    async def update(self, state: RecipientSequenceState) -> RecipientSequenceState:
        row = await self._session.get(RecipientSequenceStateRow, state.id)
        if row is None:
            raise ValueError(f"Recipient state {state.id} not found")
        state.updated_at = _now()
        apply_entity(row, state)
        await self._session.flush()
        return to_entity(row, RecipientSequenceState)

    async def set_paused_all(self, campaign_id: str, paused: bool) -> int:
        result = await self._session.execute(
            update(RecipientSequenceStateRow)
            .where(RecipientSequenceStateRow.campaign_id == campaign_id)
            .values(paused=paused, updated_at=_now())
        )
        return result.rowcount or 0

    async def set_replied(
        self, campaign_id: str, recipient_email: str, replied: bool
    ) -> None:
        await self._session.execute(
            update(RecipientSequenceStateRow)
            .where(
                RecipientSequenceStateRow.campaign_id == campaign_id,
                RecipientSequenceStateRow.recipient_email == recipient_email,
            )
            .values(replied=replied, updated_at=_now())
        )


class SqlTrackingEventRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, event: TrackingEvent) -> None:
        row = TrackingEventRow(id=event.id)
        apply_entity(row, event)
        self._session.add(row)
        await self._session.flush()

    async def email_job_exists(self, email_job_id: str) -> bool:
        return await self._session.get(EmailJobRow, email_job_id) is not None

    async def unique_event_count(
        self, campaign_id: str, event_type: TrackingEventType
    ) -> int:
        count = await self._session.scalar(
            select(func.count(func.distinct(TrackingEventRow.email_job_id)))
            .select_from(TrackingEventRow)
            .join(EmailJobRow, TrackingEventRow.email_job_id == EmailJobRow.id)
            .where(
                EmailJobRow.campaign_id == campaign_id,
                TrackingEventRow.event_type == str(event_type),
            )
        )
        return int(count or 0)

    async def list_for_campaign_jobs(
        self, campaign_id: str
    ) -> dict[str, list[TrackingEvent]]:
        rows = await self._session.scalars(
            select(TrackingEventRow)
            .join(EmailJobRow, TrackingEventRow.email_job_id == EmailJobRow.id)
            .where(EmailJobRow.campaign_id == campaign_id)
        )
        grouped: dict[str, list[TrackingEvent]] = {}
        for row in rows:
            grouped.setdefault(row.email_job_id, []).append(to_entity(row, TrackingEvent))
        return grouped

    async def list_click_urls(self, campaign_id: str) -> list[str]:
        rows = await self._session.scalars(
            select(TrackingEventRow.url)
            .join(EmailJobRow, TrackingEventRow.email_job_id == EmailJobRow.id)
            .where(
                EmailJobRow.campaign_id == campaign_id,
                TrackingEventRow.event_type == str(TrackingEventType.CLICK),
                TrackingEventRow.url.is_not(None),
            )
        )
        return [url for url in rows if url]
