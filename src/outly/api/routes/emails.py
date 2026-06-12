from fastapi import APIRouter, Request

from ..deps import CurrentUser, ServicesDep
from ..serialization import serialize

router = APIRouter(prefix="/emails")


@router.get("/search")
async def search_emails(request: Request, user: CurrentUser, services: ServicesDep):
    params = dict(request.query_params)
    return serialize(await services.emails.search(user["id"], params))


@router.get("/schedule")
async def scheduled_emails(
    user: CurrentUser, services: ServicesDep, limit: int = 50, offset: int = 0
):
    return serialize(await services.emails.list_scheduled(user["id"], limit, offset))


@router.get("/sent")
async def sent_emails(
    user: CurrentUser, services: ServicesDep, limit: int = 50, offset: int = 0
):
    return serialize(await services.emails.list_sent(user["id"], limit, offset))


@router.get("/sender/{sender_id}")
async def emails_by_sender(sender_id: str, user: CurrentUser, services: ServicesDep):
    return serialize(await services.emails.list_by_sender(user["id"], sender_id))


@router.patch("/{email_id}/star")
async def toggle_star(email_id: str, user: CurrentUser, services: ServicesDep):
    return serialize(await services.emails.toggle_star(user["id"], email_id))


@router.patch("/{email_id}/replied")
async def toggle_replied(email_id: str, user: CurrentUser, services: ServicesDep):
    return serialize(await services.emails.toggle_replied(user["id"], email_id))
