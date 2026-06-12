import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    google_id: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProviderProfileRow(Base):
    __tablename__ = "provider_profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    provider_name: Mapped[str] = mapped_column(String(255), unique=True)
    smtp_host_pattern: Mapped[str] = mapped_column(String(255), unique=True)
    per_minute_limit: Mapped[int] = mapped_column(Integer)
    per_hour_limit: Mapped[int] = mapped_column(Integer)
    per_day_limit: Mapped[int] = mapped_column(Integer)


class SenderRow(Base):
    __tablename__ = "senders"
    __table_args__ = (
        UniqueConstraint("user_id", "email"),
        Index("ix_senders_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"))
    email: Mapped[str] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    app_password: Mapped[str] = mapped_column(Text, default="")
    smtp_host: Mapped[str] = mapped_column(String(255), default="smtp.gmail.com")
    smtp_port: Mapped[int] = mapped_column(Integer, default=465)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_limit: Mapped[int] = mapped_column(Integer, default=500)
    hourly_limit: Mapped[int | None] = mapped_column(Integer)
    provider_profile_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("provider_profiles.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailCampaignRow(Base):
    __tablename__ = "email_campaigns"
    __table_args__ = (
        Index("ix_email_campaigns_user_id", "user_id"),
        Index("ix_email_campaigns_sender_id", "sender_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"))
    sender_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("senders.id"))
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delay_seconds: Mapped[int] = mapped_column(Integer)
    hourly_limit: Mapped[int] = mapped_column(Integer)
    total_recipients: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")
    pause_reason: Mapped[str | None] = mapped_column(String(255))
    track_opens: Mapped[bool] = mapped_column(Boolean, default=True)
    track_clicks: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailJobRow(Base):
    __tablename__ = "email_jobs"
    __table_args__ = (
        Index("ix_email_jobs_campaign_id", "campaign_id"),
        Index("ix_email_jobs_scheduled_at", "scheduled_at"),
        Index("ix_email_jobs_status", "status"),
        Index("ix_email_jobs_sender_id", "sender_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(String(32), ForeignKey("email_campaigns.id"))
    sender_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("senders.id"))
    to_email: Mapped[str] = mapped_column(String(255))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    error: Mapped[str | None] = mapped_column(Text)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False)
    column_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    is_replied: Mapped[bool] = mapped_column(Boolean, default=False)
    sequence_step_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("sequence_steps.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CampaignSenderRow(Base):
    __tablename__ = "campaign_senders"
    __table_args__ = (
        UniqueConstraint("campaign_id", "sender_id"),
        Index("ix_campaign_senders_campaign_id", "campaign_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("email_campaigns.id", ondelete="CASCADE")
    )
    sender_id: Mapped[str] = mapped_column(String(32), ForeignKey("senders.id"))
    rotation_order: Mapped[int] = mapped_column(Integer)


class RateLimitCounterRow(Base):
    __tablename__ = "rate_limit_counters"
    __table_args__ = (
        UniqueConstraint("sender_id", "minute_window"),
        Index("ix_rate_limit_counters_sender_hour", "sender_id", "hour_window"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    sender_id: Mapped[str] = mapped_column(String(32))
    hour_window: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    minute_window: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RefreshTokenRow(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    token: Mapped[str] = mapped_column(String(512), unique=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AttachmentRow(Base):
    __tablename__ = "attachments"
    __table_args__ = (Index("ix_attachments_campaign_id", "campaign_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("email_campaigns.id", ondelete="CASCADE")
    )
    url: Mapped[str] = mapped_column(String(1024))
    filename: Mapped[str] = mapped_column(String(512))
    size: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(255))


class EmailTemplateRow(Base):
    __tablename__ = "email_templates"
    __table_args__ = (
        UniqueConstraint("user_id", "name"),
        Index("ix_email_templates_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SequenceStepRow(Base):
    __tablename__ = "sequence_steps"
    __table_args__ = (
        UniqueConstraint("campaign_id", "step_number"),
        Index("ix_sequence_steps_campaign_id", "campaign_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("email_campaigns.id", ondelete="CASCADE")
    )
    step_number: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    wait_days: Mapped[int] = mapped_column(Integer)


class RecipientSequenceStateRow(Base):
    __tablename__ = "recipient_sequence_states"
    __table_args__ = (
        UniqueConstraint("campaign_id", "recipient_email"),
        Index("ix_recipient_states_campaign_id", "campaign_id"),
        Index(
            "ix_recipient_states_active",
            "campaign_id",
            "paused",
            "replied",
            "completed",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("email_campaigns.id", ondelete="CASCADE")
    )
    recipient_email: Mapped[str] = mapped_column(String(255))
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    replied: Mapped[bool] = mapped_column(Boolean, default=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    step_statuses: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WarmupScheduleRow(Base):
    __tablename__ = "warmup_schedules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    sender_id: Mapped[str] = mapped_column(String(32), ForeignKey("senders.id"), unique=True)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_days: Mapped[int] = mapped_column(Integer, default=14)
    daily_limits: Mapped[list[Any]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)


class SenderCooldownRow(Base):
    __tablename__ = "sender_cooldowns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    sender_id: Mapped[str] = mapped_column(String(32), ForeignKey("senders.id"), unique=True)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TrackingEventRow(Base):
    __tablename__ = "tracking_events"
    __table_args__ = (
        Index("ix_tracking_events_email_job_id", "email_job_id"),
        Index("ix_tracking_events_event_type", "event_type"),
        Index("ix_tracking_events_job_type", "email_job_id", "event_type"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email_job_id: Mapped[str] = mapped_column(String(32), ForeignKey("email_jobs.id"))
    event_type: Mapped[str] = mapped_column(String(10))
    url: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
