from dataclasses import dataclass
from typing import Annotated, Any, AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.attachments import AttachmentService
from ..application.auth import AuthService
from ..application.campaigns import CampaignService
from ..application.emails import EmailService
from ..application.errors import Unauthorized
from ..application.ports import EmailQueue, FileStorage
from ..application.senders import SenderService
from ..application.sequences import SequenceService
from ..application.templates import TemplateService
from ..application.throttling import AdaptiveThrottle, ThrottleEngine, WarmupEvaluator
from ..application.tracking import TrackingService
from ..adapters.db.repositories import (
    SqlAttachmentRepository,
    SqlCampaignRepository,
    SqlCampaignSenderRepository,
    SqlEmailJobRepository,
    SqlProviderProfileRepository,
    SqlRateLimitRepository,
    SqlRecipientStateRepository,
    SqlRefreshTokenRepository,
    SqlSenderCooldownRepository,
    SqlSenderRepository,
    SqlSequenceStepRepository,
    SqlTemplateRepository,
    SqlTrackingEventRepository,
    SqlUserRepository,
    SqlWarmupScheduleRepository,
)
from ..config import Settings


@dataclass
class Services:
    session: AsyncSession
    settings: Settings
    queue: EmailQueue
    storage: FileStorage
    users: SqlUserRepository
    email_jobs: SqlEmailJobRepository
    campaign_repo: SqlCampaignRepository
    auth: AuthService
    senders: SenderService
    campaigns: CampaignService
    emails: EmailService
    sequences: SequenceService
    templates: TemplateService
    attachments: AttachmentService
    tracking: TrackingService


def build_services(session: AsyncSession, state: Any) -> Services:
    settings: Settings = state.settings
    user_repo = SqlUserRepository(session)
    refresh_repo = SqlRefreshTokenRepository(session)
    sender_repo = SqlSenderRepository(session)
    provider_repo = SqlProviderProfileRepository(session)
    warmup_repo = SqlWarmupScheduleRepository(session)
    cooldown_repo = SqlSenderCooldownRepository(session)
    rate_repo = SqlRateLimitRepository(session)
    campaign_repo = SqlCampaignRepository(session)
    campaign_sender_repo = SqlCampaignSenderRepository(session)
    email_job_repo = SqlEmailJobRepository(session)
    attachment_repo = SqlAttachmentRepository(session)
    template_repo = SqlTemplateRepository(session)
    step_repo = SqlSequenceStepRepository(session)
    state_repo = SqlRecipientStateRepository(session)
    tracking_repo = SqlTrackingEventRepository(session)

    warmup = WarmupEvaluator(warmup_repo)
    adaptive = AdaptiveThrottle(
        email_job_repo, cooldown_repo, settings.cooldown_duration_ms
    )
    throttle = ThrottleEngine(
        sender_repo, provider_repo, rate_repo, email_job_repo, campaign_sender_repo,
        warmup, adaptive,
    )

    return Services(
        session=session,
        settings=settings,
        queue=state.queue,
        storage=state.storage,
        users=user_repo,
        email_jobs=email_job_repo,
        campaign_repo=campaign_repo,
        auth=AuthService(user_repo, refresh_repo, state.signer, state.google_verifier),
        senders=SenderService(
            sender_repo, provider_repo, warmup_repo, rate_repo, state.mailer,
            state.cipher, throttle, warmup, adaptive,
        ),
        campaigns=CampaignService(
            campaign_repo, campaign_sender_repo, email_job_repo, attachment_repo,
            step_repo, state_repo, sender_repo, rate_repo, throttle, adaptive,
        ),
        emails=EmailService(email_job_repo, campaign_repo, sender_repo, state_repo),
        sequences=SequenceService(campaign_repo, step_repo, state_repo),
        templates=TemplateService(template_repo),
        attachments=AttachmentService(attachment_repo, state.storage),
        tracking=TrackingService(tracking_repo, email_job_repo, campaign_repo),
    )


async def get_services(request: Request) -> AsyncIterator[Services]:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield build_services(session, request.app.state)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_current_user(request: Request) -> dict[str, Any]:
    auth_header = request.headers.get("authorization")
    if not auth_header:
        raise Unauthorized("Authorization header missing")
    parts = auth_header.split(" ")
    if len(parts) != 2 or parts[0] != "Bearer" or not parts[1]:
        raise Unauthorized("Invalid authorization format")
    try:
        payload = request.app.state.signer.verify_access_token(parts[1])
    except Exception as error:
        raise Unauthorized("Invalid or expired token") from error
    return {"id": payload.get("id"), "email": payload.get("email")}


ServicesDep = Annotated[Services, Depends(get_services)]
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
