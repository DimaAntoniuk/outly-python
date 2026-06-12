from fastapi import APIRouter

from ...application.errors import NotFound
from ..deps import CurrentUser, ServicesDep
from ..serialization import serialize

router = APIRouter(prefix="/users")


@router.get("/")
async def get_user(user: CurrentUser, services: ServicesDep):
    found = await services.users.get(user["id"])
    if found is None:
        raise NotFound("User not found")
    return serialize(found)


@router.get("/emails")
async def get_user_emails(
    user: CurrentUser, services: ServicesDep, limit: int = 50, offset: int = 0
):
    jobs = await services.emails.list_all(user["id"], limit, offset)
    result = []
    for job in jobs:
        campaign = await services.campaign_repo.get(job.campaign_id)
        result.append(
            {
                "email": serialize(job),
                "campaign": {
                    "subject": campaign.subject if campaign else None,
                    "body": campaign.body if campaign else None,
                },
            }
        )
    return result
