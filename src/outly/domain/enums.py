from enum import StrEnum


class CampaignStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    SENDING = "SENDING"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class EmailStatus(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    SENT = "SENT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TrackingEventType(StrEnum):
    OPEN = "OPEN"
    CLICK = "CLICK"
