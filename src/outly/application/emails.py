from typing import Any

from ..domain.entities import EmailJob
from ..domain.enums import EmailStatus
from ..domain.validation import (
    parse_iso_date,
    validate_date_field,
    validate_date_range,
    validate_search_query,
    validate_status_param,
)
from .errors import BadRequest, Conflict, Forbidden, NotFound
from .ports import (
    CampaignRepository,
    EmailJobRepository,
    RecipientStateRepository,
    SenderRepository,
)
from .throttling import utc_now


class EmailService:
    def __init__(
        self,
        email_job_repo: EmailJobRepository,
        campaign_repo: CampaignRepository,
        sender_repo: SenderRepository,
        recipient_state_repo: RecipientStateRepository,
    ):
        self._email_job_repo = email_job_repo
        self._campaign_repo = campaign_repo
        self._sender_repo = sender_repo
        self._recipient_state_repo = recipient_state_repo

    async def search(self, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("q")
        for check in (
            validate_search_query(query),
            validate_status_param(params.get("status"), tuple(EmailStatus)),
            validate_date_range(params.get("dateFrom"), params.get("dateTo")),
            validate_date_field(params.get("dateField")),
        ):
            if not check.valid:
                raise BadRequest(check.message or "Invalid search parameters")

        sender_id = params.get("senderId")
        if sender_id and await self._sender_repo.get_owned(sender_id, user_id) is None:
            raise Forbidden("Sender not found or not owned by you")

        results = await self._email_job_repo.search(
            user_id,
            query,
            EmailStatus(params["status"]) if params.get("status") else None,
            sender_id,
            params.get("dateField") or "createdAt",
            parse_iso_date(params.get("dateFrom")),
            parse_iso_date(params.get("dateTo")),
            True if params.get("starred") == "true" else None,
        )
        return {"results": results, "total": len(results), "filters": params}

    async def list_scheduled(self, user_id: str, limit: int, offset: int) -> list[EmailJob]:
        return await self._email_job_repo.list_for_user(
            user_id, EmailStatus.PENDING, "scheduled_at", False, min(limit, 200), offset
        )

    async def list_sent(self, user_id: str, limit: int, offset: int) -> list[EmailJob]:
        return await self._email_job_repo.list_for_user(
            user_id, EmailStatus.SENT, "sent_at", True, min(limit, 200), offset
        )

    async def list_all(self, user_id: str, limit: int, offset: int) -> list[EmailJob]:
        return await self._email_job_repo.list_for_user(
            user_id, None, "created_at", True, min(limit, 200), offset
        )

    async def list_by_sender(self, user_id: str, sender_id: str) -> list[EmailJob]:
        if await self._sender_repo.get_owned(sender_id, user_id) is None:
            raise NotFound("Sender not found")
        return await self._email_job_repo.list_for_sender(sender_id, user_id)

    async def toggle_star(self, user_id: str, email_id: str) -> EmailJob:
        job = await self._email_job_repo.get_owned(email_id, user_id)
        if job is None:
            raise NotFound("Email not found")
        job.is_starred = not job.is_starred
        job.updated_at = utc_now()
        return await self._email_job_repo.update(job)

    async def toggle_replied(self, user_id: str, email_id: str) -> EmailJob:
        job = await self._email_job_repo.get_owned(email_id, user_id)
        if job is None:
            raise NotFound("Email not found")
        if job.status != EmailStatus.SENT:
            raise Conflict("Only sent emails can be marked as replied")
        job.is_replied = not job.is_replied
        job.updated_at = utc_now()
        updated = await self._email_job_repo.update(job)
        try:
            await self._recipient_state_repo.set_replied(
                job.campaign_id, job.to_email, job.is_replied
            )
        except Exception:
            pass
        return updated
