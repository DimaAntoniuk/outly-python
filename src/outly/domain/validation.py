from dataclasses import dataclass
from datetime import datetime

MAX_FOLLOW_UP_STEPS = 5
MAX_QUERY_LENGTH = 200
ALLOWED_DATE_FIELDS = ("createdAt", "scheduledAt", "sentAt")
EMAIL_PATTERN = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    message: str | None = None


def validate_sequence_steps(steps: object) -> ValidationResult:
    if not isinstance(steps, list):
        return ValidationResult(False, "steps must be an array")
    if len(steps) > MAX_FOLLOW_UP_STEPS:
        return ValidationResult(False, f"A maximum of {MAX_FOLLOW_UP_STEPS} follow-up steps is allowed")
    for index, step in enumerate(steps):
        subject = step.get("subject") if isinstance(step, dict) else None
        body = step.get("body") if isinstance(step, dict) else None
        wait_days = step.get("waitDays") if isinstance(step, dict) else None
        if not isinstance(subject, str) or not subject.strip():
            return ValidationResult(False, f"Step {index + 1}: subject is required")
        if not isinstance(body, str) or not body.strip():
            return ValidationResult(False, f"Step {index + 1}: body is required")
        if not isinstance(wait_days, int) or isinstance(wait_days, bool) or wait_days < 1:
            return ValidationResult(False, f"Step {index + 1}: waitDays must be an integer >= 1")
    return ValidationResult(True)


def validate_search_query(query: str | None) -> ValidationResult:
    if query is not None and len(query) > MAX_QUERY_LENGTH:
        return ValidationResult(False, f"Search query must be at most {MAX_QUERY_LENGTH} characters")
    return ValidationResult(True)


def validate_status_param(status: str | None, allowed: tuple[str, ...]) -> ValidationResult:
    if not status:
        return ValidationResult(True)
    if status not in allowed:
        return ValidationResult(False, f"Invalid status. Allowed: {', '.join(allowed)}")
    return ValidationResult(True)


def parse_iso_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_date_range(date_from: str | None, date_to: str | None) -> ValidationResult:
    parsed_from = parse_iso_date(date_from)
    parsed_to = parse_iso_date(date_to)
    if date_from and parsed_from is None:
        return ValidationResult(False, "dateFrom must be a valid ISO 8601 date")
    if date_to and parsed_to is None:
        return ValidationResult(False, "dateTo must be a valid ISO 8601 date")
    if parsed_from and parsed_to and parsed_from > parsed_to:
        return ValidationResult(False, "dateFrom must be before dateTo")
    return ValidationResult(True)


def validate_date_field(date_field: str | None) -> ValidationResult:
    if not date_field:
        return ValidationResult(True)
    if date_field not in ALLOWED_DATE_FIELDS:
        return ValidationResult(False, f"Invalid dateField. Allowed: {', '.join(ALLOWED_DATE_FIELDS)}")
    return ValidationResult(True)
