from fastapi import APIRouter, Request, UploadFile

from ...application.errors import BadRequest
from ..deps import CurrentUser, ServicesDep

router = APIRouter(prefix="/attachments")


@router.post("/upload")
async def upload_attachments(
    request: Request, user: CurrentUser, services: ServicesDep
):
    form = await request.form()
    uploads = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
    if not uploads:
        raise BadRequest("No files provided")
    files = []
    for upload in uploads:
        content = await upload.read()
        files.append(
            (upload.filename or "file", upload.content_type or "application/octet-stream", content)
        )
    return await services.attachments.upload(files)


@router.delete("/delete")
async def delete_attachment(request: Request, user: CurrentUser, services: ServicesDep):
    body = await request.json()
    try:
        await services.attachments.delete(user["id"], body.get("url"))
    except ValueError as error:
        raise BadRequest("Invalid attachment URL") from error
    return {"message": "Attachment deleted"}
