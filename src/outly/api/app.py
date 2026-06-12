import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ..adapters.db.seed import seed_provider_profiles
from ..adapters.db.session import create_engine, create_session_factory, init_db
from ..adapters.google import GoogleIdTokenVerifier
from ..adapters.queue import create_email_queue
from ..adapters.security import AesCredentialCipher, JwtTokenSigner
from ..adapters.smtp import AiosmtplibMailer
from ..adapters.storage import LocalFileStorage
from ..application.errors import AppError
from ..application.ports import EmailQueue
from ..config import Settings, get_settings
from .routes import (
    attachments,
    auth,
    campaigns,
    emails,
    senders,
    sequences,
    templates,
    tracking,
    tracking_metrics,
    users,
)

logger = logging.getLogger(__name__)


class NullEmailQueue:
    async def enqueue_send(self, email_job_id: str, delay_ms: int = 0) -> None:
        logger.warning("Email queue unavailable; job %s not enqueued", email_job_id)


def create_app(
    settings: Settings | None = None, queue: EmailQueue | None = None
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_engine(settings.database_url)
        await init_db(engine)
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            await seed_provider_profiles(session)

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.cipher = AesCredentialCipher(settings.encryption_key)
        app.state.signer = JwtTokenSigner(
            settings.jwt_access_secret,
            settings.jwt_refresh_secret,
            settings.access_token_expires,
            settings.refresh_token_expires,
        )
        app.state.mailer = AiosmtplibMailer()
        app.state.google_verifier = GoogleIdTokenVerifier(settings.google_client_id)
        app.state.storage = LocalFileStorage(
            settings.attachment_dir, settings.server_base_url
        )
        if queue is not None:
            app.state.queue = queue
        else:
            try:
                app.state.queue = await create_email_queue(settings.redis_url)
            except Exception:
                logger.exception("Could not connect to Redis; email enqueueing disabled")
                app.state.queue = NullEmailQueue()
        yield
        await engine.dispose()

    app = FastAPI(title="Outly", lifespan=lifespan)

    origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    if settings.client_url:
        origins.append(settings.client_url)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, error: AppError):
        return JSONResponse(status_code=error.status_code, content={"message": error.message})

    @app.get("/")
    async def home():
        return PlainTextResponse("This is the home route")

    app.include_router(tracking.router)
    app.include_router(auth.router)
    if settings.env == "development":
        app.include_router(auth.dev_router)
    app.include_router(users.router)
    app.include_router(senders.router)
    app.include_router(campaigns.router)
    app.include_router(emails.router)
    app.include_router(attachments.router)
    app.include_router(templates.router)
    app.include_router(sequences.router)
    app.include_router(tracking_metrics.router)

    files_dir = Path(settings.attachment_dir)
    files_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=str(files_dir)), name="files")

    return app


app = create_app()
