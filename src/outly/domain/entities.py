from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import CampaignStatus, EmailStatus, TrackingEventType


@dataclass
class User:
    id: str
    google_id: str
    email: str
    name: str
    avatar_url: str | None
    created_at: datetime


@dataclass
class Sender:
    id: str
    user_id: str
    email: str
    name: str | None
    app_password: str
    smtp_host: str
    smtp_port: int
    is_verified: bool
    daily_limit: int
    hourly_limit: int | None
    provider_profile_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass
class ProviderProfile:
    id: str
    provider_name: str
    smtp_host_pattern: str
    per_minute_limit: int
    per_hour_limit: int
    per_day_limit: int


@dataclass
class WarmupSchedule:
    id: str
    sender_id: str
    start_date: datetime
    duration_days: int
    daily_limits: list[int]
    is_active: bool
    opted_out: bool


@dataclass
class SenderCooldown:
    id: str
    sender_id: str
    consecutive_errors: int
    cooldown_until: datetime | None


@dataclass
class EmailCampaign:
    id: str
    user_id: str
    sender_id: str | None
    subject: str
    body: str
    start_time: datetime
    delay_seconds: int
    hourly_limit: int
    total_recipients: int
    status: CampaignStatus
    pause_reason: str | None
    track_opens: bool
    track_clicks: bool
    created_at: datetime


@dataclass
class CampaignSender:
    id: str
    campaign_id: str
    sender_id: str
    rotation_order: int


@dataclass
class EmailJob:
    id: str
    campaign_id: str
    sender_id: str | None
    to_email: str
    scheduled_at: datetime
    sent_at: datetime | None
    status: EmailStatus
    error: str | None
    is_starred: bool
    column_data: dict[str, Any] | None
    is_replied: bool
    sequence_step_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass
class Attachment:
    id: str
    campaign_id: str
    url: str
    filename: str
    size: int
    mime_type: str


@dataclass
class EmailTemplate:
    id: str
    user_id: str
    name: str
    subject: str
    body: str
    created_at: datetime
    updated_at: datetime


@dataclass
class SequenceStep:
    id: str
    campaign_id: str
    step_number: int
    subject: str
    body: str
    wait_days: int


@dataclass
class RecipientSequenceState:
    id: str
    campaign_id: str
    recipient_email: str
    current_step: int
    paused: bool
    replied: bool
    completed: bool
    step_statuses: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class RefreshToken:
    id: str
    token: str
    user_id: str
    revoked: bool
    expires_at: datetime
    created_at: datetime


@dataclass
class TrackingEvent:
    id: str
    email_job_id: str
    event_type: TrackingEventType
    url: str | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime
