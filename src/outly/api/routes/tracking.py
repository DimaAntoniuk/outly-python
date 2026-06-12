from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from ...application.errors import BadRequest
from ...application.tracking import TRANSPARENT_GIF
from ..deps import ServicesDep

router = APIRouter(prefix="/track")

PIXEL_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip_address = forwarded.split(",")[0].strip()
    else:
        ip_address = request.client.host if request.client else None
    return ip_address, request.headers.get("user-agent")


@router.get("/open/{email_job_id}")
async def track_open(email_job_id: str, request: Request, services: ServicesDep):
    ip_address, user_agent = _client_meta(request)
    await services.tracking.record_open(email_job_id, ip_address, user_agent)
    return Response(content=TRANSPARENT_GIF, media_type="image/gif", headers=PIXEL_HEADERS)


@router.get("/click/{email_job_id}")
async def track_click(email_job_id: str, request: Request, services: ServicesDep):
    url = request.query_params.get("url")
    if not url:
        raise BadRequest("Missing url parameter")
    try:
        decoded = unquote(url)
    except Exception:
        decoded = url
    ip_address, user_agent = _client_meta(request)
    await services.tracking.record_click(email_job_id, decoded, ip_address, user_agent)
    return RedirectResponse(decoded or "/", status_code=302)
