from fastapi import APIRouter, Request

from ..deps import CurrentUser, ServicesDep
from ..serialization import serialize

router = APIRouter(prefix="/templates")


@router.post("/", status_code=201)
async def create_template(request: Request, user: CurrentUser, services: ServicesDep):
    body = await request.json()
    return serialize(
        await services.templates.create(
            user["id"], body.get("name"), body.get("subject"), body.get("body")
        )
    )


@router.get("/")
async def get_templates(user: CurrentUser, services: ServicesDep):
    return serialize(await services.templates.list_for_user(user["id"]))


@router.put("/{template_id}")
async def update_template(
    template_id: str, request: Request, user: CurrentUser, services: ServicesDep
):
    body = await request.json()
    return serialize(
        await services.templates.update(
            user["id"], template_id, body.get("name"), body.get("subject"), body.get("body")
        )
    )


@router.delete("/{template_id}")
async def delete_template(template_id: str, user: CurrentUser, services: ServicesDep):
    await services.templates.delete(user["id"], template_id)
    return {"message": "Template deleted"}
