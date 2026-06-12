from fastapi import APIRouter

from ..deps import CurrentUser, ServicesDep
from ..serialization import serialize

router = APIRouter(prefix="/api/tracking")


@router.get("/campaigns/{campaign_id}")
async def campaign_metrics(campaign_id: str, user: CurrentUser, services: ServicesDep):
    return serialize(await services.tracking.campaign_metrics(user["id"], campaign_id))


@router.get("/campaigns/{campaign_id}/emails")
async def campaign_emails(campaign_id: str, user: CurrentUser, services: ServicesDep):
    return serialize(await services.tracking.campaign_emails(user["id"], campaign_id))


@router.get("/campaigns/{campaign_id}/links")
async def campaign_links(campaign_id: str, user: CurrentUser, services: ServicesDep):
    return serialize(await services.tracking.campaign_links(user["id"], campaign_id))
