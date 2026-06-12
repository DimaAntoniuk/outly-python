from .enums import CampaignStatus, EmailStatus

ALLOWED_TRANSITIONS: dict[CampaignStatus, frozenset[CampaignStatus]] = {
    CampaignStatus.SCHEDULED: frozenset(
        {CampaignStatus.SENDING, CampaignStatus.PAUSED, CampaignStatus.CANCELLED}
    ),
    CampaignStatus.SENDING: frozenset(
        {CampaignStatus.PAUSED, CampaignStatus.CANCELLED, CampaignStatus.COMPLETED}
    ),
    CampaignStatus.PAUSED: frozenset(
        {CampaignStatus.SENDING, CampaignStatus.CANCELLED, CampaignStatus.COMPLETED}
    ),
    CampaignStatus.CANCELLED: frozenset(),
    CampaignStatus.COMPLETED: frozenset(),
}

TERMINAL_EMAIL_STATUSES = frozenset(
    {EmailStatus.SENT, EmailStatus.FAILED, EmailStatus.CANCELLED}
)


def is_valid_transition(current: CampaignStatus, target: CampaignStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def is_terminal_email_status(status: EmailStatus) -> bool:
    return status in TERMINAL_EMAIL_STATUSES
