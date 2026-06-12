from fastapi import APIRouter, Request, Response

from ...application.errors import AppError
from ..deps import ServicesDep

router = APIRouter(prefix="/auth")

REFRESH_COOKIE = "refreshToken"
REFRESH_COOKIE_PATH = "/auth/refresh"
REFRESH_COOKIE_MAX_AGE = 30 * 24 * 60 * 60


def _set_refresh_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=REFRESH_COOKIE_MAX_AGE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=secure,
        samesite="strict",
    )


@router.post("/google")
async def google_login(request: Request, response: Response, services: ServicesDep):
    body = await request.json()
    try:
        result = await services.auth.google_login(body.get("idToken"))
    except AppError:
        raise
    except Exception as error:
        raise AppError(500, "Google login failed. Please try again.") from error
    _set_refresh_cookie(
        response, result.refresh_token, services.settings.env == "production"
    )
    return {
        "accessToken": result.access_token,
        "user": {
            "id": result.user.id,
            "email": result.user.email,
            "name": result.user.name,
            "avatarUrl": result.user.avatar_url,
        },
    }


dev_router = APIRouter(prefix="/auth")


@dev_router.post("/dev-login")
async def dev_login(request: Request, response: Response, services: ServicesDep):
    body = await request.json()
    result = await services.auth.dev_login(body.get("email"), body.get("name"))
    _set_refresh_cookie(response, result.refresh_token, secure=False)
    return {
        "accessToken": result.access_token,
        "user": {
            "id": result.user.id,
            "email": result.user.email,
            "name": result.user.name,
            "avatarUrl": result.user.avatar_url,
        },
    }


@router.post("/refresh")
async def refresh(request: Request, response: Response, services: ServicesDep):
    try:
        access_token, refresh_token = await services.auth.refresh(
            request.cookies.get(REFRESH_COOKIE)
        )
    except AppError:
        raise
    except Exception as error:
        raise AppError(500, "Failed to refresh access token") from error
    _set_refresh_cookie(response, refresh_token, services.settings.env == "production")
    return {"accessToken": access_token}


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, services: ServicesDep):
    try:
        await services.auth.logout(request.cookies.get(REFRESH_COOKIE))
    except AppError:
        raise
    except Exception as error:
        raise AppError(500, "Logout failed") from error
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
    return Response(status_code=204)
