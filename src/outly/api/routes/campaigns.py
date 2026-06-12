from fastapi import APIRouter, Request

from ..deps import CurrentUser, ServicesDep
from ..serialization import serialize

router = APIRouter(prefix="/campaigns")


@router.get("/search")
async def search_campaigns(request: Request, user: CurrentUser, services: ServicesDep):
    params = dict(request.query_params)
    result = await services.campaigns.search(user["id"], params)
    return serialize(result)


@router.get("/complete")
async def completed_campaigns(user: CurrentUser, services: ServicesDep):
    return serialize(await services.campaigns.list_completed(user["id"]))


@router.post("/", status_code=201)
async def create_campaign(request: Request, user: CurrentUser, services: ServicesDep):
    body = await request.json()
    result = await services.campaigns.create(user["id"], body)
    await services.session.commit()
    for job_id, delay_ms in result.enqueue_plan:
        await services.queue.enqueue_send(job_id, delay_ms)
    return {
        "message": "Campaign created",
        "campaignId": result.campaign_id,
        "senderPool": result.sender_pool,
    }


@router.get("/")
async def all_campaigns(user: CurrentUser, services: ServicesDep):
    return serialize(await services.campaigns.list_for_user(user["id"]))


@router.get("/{campaign_id}")
async def campaign_detail(campaign_id: str, user: CurrentUser, services: ServicesDep):
    return serialize(await services.campaigns.get_detail(user["id"], campaign_id))


@router.get("/{campaign_id}/throttle-status")
async def campaign_throttle_status(
    campaign_id: str, user: CurrentUser, services: ServicesDep
):
    return serialize(await services.campaigns.throttle_status(user["id"], campaign_id))


@router.patch("/{campaign_id}/pause")
async def pause_campaign(campaign_id: str, user: CurrentUser, services: ServicesDep):
    return serialize(await services.campaigns.pause(user["id"], campaign_id))


@router.patch("/{campaign_id}/resume")
async def resume_campaign(campaign_id: str, user: CurrentUser, services: ServicesDep):
    result = await services.campaigns.resume(user["id"], campaign_id)
    await services.session.commit()
    for job_id, delay_ms in result.enqueue_plan:
        await services.queue.enqueue_send(job_id, delay_ms)
    return serialize(result.campaign)


@router.patch("/{campaign_id}/cancel")
async def cancel_campaign(campaign_id: str, user: CurrentUser, services: ServicesDep):
    return serialize(await services.campaigns.cancel(user["id"], campaign_id))
