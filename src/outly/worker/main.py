import logging

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession

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
    SqlWarmupScheduleRepository,
)
from ..adapters.db.seed import seed_provider_profiles
from ..adapters.db.session import create_engine, create_session_factory, init_db
from ..adapters.queue import ArqEmailQueue
from ..adapters.security import AesCredentialCipher
from ..adapters.smtp import AiosmtplibMailer
from ..adapters.storage import LocalFileStorage
from ..application.delivery import CampaignCompletionChecker, SendEmailUseCase
from ..application.maintenance import (
    AutoResumeUseCase,
    SequenceSchedulerUseCase,
    SweepUseCase,
)
from ..application.throttling import AdaptiveThrottle, ThrottleEngine, WarmupEvaluator
from ..config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _throttle(session: AsyncSession, settings) -> tuple[ThrottleEngine, AdaptiveThrottle, WarmupEvaluator]:
    email_job_repo = SqlEmailJobRepository(session)
    warmup = WarmupEvaluator(SqlWarmupScheduleRepository(session))
    adaptive = AdaptiveThrottle(
        email_job_repo, SqlSenderCooldownRepository(session), settings.cooldown_duration_ms
    )
    throttle = ThrottleEngine(
        SqlSenderRepository(session),
        SqlProviderProfileRepository(session),
        SqlRateLimitRepository(session),
        email_job_repo,
        SqlCampaignSenderRepository(session),
        warmup,
        adaptive,
    )
    return throttle, adaptive, warmup


async def startup(ctx):
    settings = get_settings()
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        await seed_provider_profiles(session)

    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["session_factory"] = session_factory
    ctx["queue"] = ArqEmailQueue(ctx["redis"])
    ctx["mailer"] = AiosmtplibMailer()
    ctx["cipher"] = AesCredentialCipher(settings.encryption_key)
    ctx["storage"] = LocalFileStorage(settings.attachment_dir, settings.server_base_url)

    async with session_factory() as session:
        sweep = _sweep_use_case(ctx, session)
        await sweep.recover_orphaned_jobs()
        await sweep.sweep_stuck_campaigns()
        await session.commit()
    logger.info("Worker started")


async def shutdown(ctx):
    await ctx["engine"].dispose()


def _sweep_use_case(ctx, session: AsyncSession) -> SweepUseCase:
    return SweepUseCase(
        SqlCampaignRepository(session),
        SqlEmailJobRepository(session),
        SqlRecipientStateRepository(session),
        ctx["queue"],
        ctx["settings"].stale_sending_threshold_ms,
    )


async def send_email(ctx, email_job_id: str):
    async with ctx["session_factory"]() as session:
        throttle, _, _ = _throttle(session, ctx["settings"])
        campaign_repo = SqlCampaignRepository(session)
        email_job_repo = SqlEmailJobRepository(session)
        use_case = SendEmailUseCase(
            email_job_repo,
            campaign_repo,
            SqlCampaignSenderRepository(session),
            SqlSenderRepository(session),
            SqlSequenceStepRepository(session),
            SqlRecipientStateRepository(session),
            SqlAttachmentRepository(session),
            throttle,
            ctx["mailer"],
            ctx["cipher"],
            ctx["storage"],
            ctx["queue"],
            CampaignCompletionChecker(campaign_repo, email_job_repo),
            ctx["settings"].tracking_base_url,
        )
        await use_case.execute(email_job_id)
        await session.commit()


async def run_sequence_scheduler(ctx):
    async with ctx["session_factory"]() as session:
        use_case = SequenceSchedulerUseCase(
            SqlCampaignRepository(session),
            SqlSequenceStepRepository(session),
            SqlRecipientStateRepository(session),
            SqlEmailJobRepository(session),
            SqlCampaignSenderRepository(session),
            ctx["queue"],
        )
        await use_case.run()
        await session.commit()


async def run_auto_resume(ctx):
    async with ctx["session_factory"]() as session:
        throttle, _, _ = _throttle(session, ctx["settings"])
        use_case = AutoResumeUseCase(
            SqlCampaignRepository(session),
            SqlEmailJobRepository(session),
            SqlRateLimitRepository(session),
            SqlRefreshTokenRepository(session),
            throttle,
            ctx["queue"],
        )
        await use_case.run()
        await session.commit()


async def run_stale_sweep(ctx):
    async with ctx["session_factory"]() as session:
        await _sweep_use_case(ctx, session).sweep_stale_sending_jobs()
        await session.commit()


async def run_stuck_campaign_sweep(ctx):
    async with ctx["session_factory"]() as session:
        await _sweep_use_case(ctx, session).sweep_stuck_campaigns()
        await session.commit()


class WorkerSettings:
    functions = [send_email]
    cron_jobs = [
        cron(run_sequence_scheduler, minute={0, 15, 30, 45}, run_at_startup=False),
        cron(run_auto_resume, minute={0}, run_at_startup=False),
        cron(run_stale_sweep, minute=set(range(0, 60, 2)), run_at_startup=False),
        cron(run_stuck_campaign_sweep, minute=set(range(0, 60, 5)), run_at_startup=False),
    ]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = get_settings().worker_concurrency
    max_tries = 3
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
