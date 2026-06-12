from fastapi import APIRouter

from ..deps import CurrentUser, ServicesDep
from ..serialization import serialize

router = APIRouter(prefix="/campaigns/{campaign_id}/sequence")


@router.get("/")
async def get_sequence(campaign_id: str, user: CurrentUser, services: ServicesDep):
    return serialize(await services.sequences.get_structure(user["id"], campaign_id))


@router.patch("/pause")
async def pause_sequence(campaign_id: str, user: CurrentUser, services: ServicesDep):
    count = await services.sequences.pause_all(user["id"], campaign_id)
    return {"message": "Sequence paused", "count": count}


@router.patch("/resume")
async def resume_sequence(campaign_id: str, user: CurrentUser, services: ServicesDep):
    count = await services.sequences.resume_all(user["id"], campaign_id)
    return {"message": "Sequence resumed", "count": count}


@router.patch("/stop")
async def stop_sequence(campaign_id: str, user: CurrentUser, services: ServicesDep):
    count = await services.sequences.stop_all(user["id"], campaign_id)
    return {"message": "Sequence stopped", "count": count}


@router.patch("/recipients/{recipient_id}/pause")
async def pause_recipient(
    campaign_id: str, recipient_id: str, user: CurrentUser, services: ServicesDep
):
    return serialize(
        await services.sequences.pause_recipient(user["id"], campaign_id, recipient_id)
    )


@router.patch("/recipients/{recipient_id}/resume")
async def resume_recipient(
    campaign_id: str, recipient_id: str, user: CurrentUser, services: ServicesDep
):
    return serialize(
        await services.sequences.resume_recipient(user["id"], campaign_id, recipient_id)
    )


@router.patch("/recipients/{recipient_id}/stop")
async def stop_recipient(
    campaign_id: str, recipient_id: str, user: CurrentUser, services: ServicesDep
):
    return serialize(
        await services.sequences.stop_recipient(user["id"], campaign_id, recipient_id)
    )
