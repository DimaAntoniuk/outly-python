from fastapi import APIRouter, Request

from ..deps import CurrentUser, ServicesDep
from ..serialization import serialize

router = APIRouter(prefix="/senders")


@router.get("/")
async def get_senders(user: CurrentUser, services: ServicesDep):
    return serialize(await services.senders.list_with_stats(user["id"]))


@router.get("/email")
async def get_sender_emails(user: CurrentUser, services: ServicesDep):
    return await services.senders.list_emails(user["id"])


@router.get("/{sender_id}")
async def get_sender(sender_id: str, user: CurrentUser, services: ServicesDep):
    return serialize(await services.senders.get_detail(user["id"], sender_id))


@router.post("/", status_code=201)
async def create_sender(request: Request, user: CurrentUser, services: ServicesDep):
    body = await request.json()
    return serialize(
        await services.senders.create(
            user["id"],
            body.get("name"),
            body.get("email"),
            body.get("appPassword"),
            body.get("skipWarmup") is True,
        )
    )


@router.patch("/{sender_id}/verify")
async def verify_sender(
    sender_id: str, request: Request, user: CurrentUser, services: ServicesDep
):
    body = await request.json()
    return serialize(
        await services.senders.verify(
            user["id"],
            sender_id,
            body.get("appPassword"),
            body.get("name"),
            body.get("skipWarmup") is True,
        )
    )
