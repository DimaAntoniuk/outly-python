import uuid
from datetime import datetime, timezone

import pytest

from outly.adapters.db.repositories import (
    SqlAttachmentRepository,
    SqlCampaignRepository,
    SqlCampaignSenderRepository,
    SqlEmailJobRepository,
    SqlProviderProfileRepository,
    SqlRateLimitRepository,
    SqlRecipientStateRepository,
    SqlSenderCooldownRepository,
    SqlSenderRepository,
    SqlSequenceStepRepository,
    SqlWarmupScheduleRepository,
)
from outly.adapters.db.models import (
    EmailCampaignRow,
    EmailJobRow,
    SenderRow,
    UserRow,
)
from outly.adapters.db.session import create_engine, create_session_factory, init_db
from outly.adapters.security import AesCredentialCipher
from outly.application.delivery import CampaignCompletionChecker, SendEmailUseCase
from outly.application.throttling import AdaptiveThrottle, ThrottleEngine, WarmupEvaluator
from conftest import FakeMailer, FakeQueue

KEY = "cd" * 32


class NullStorage:
    async def save(self, filename, content):
        raise NotImplementedError

    async def read(self, url):
        return b"data"

    async def delete(self, url):
        pass


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/delivery.db")
    await init_db(engine)
    yield create_session_factory(engine)
    await engine.dispose()


def _build_use_case(session, mailer, queue, cipher):
    email_job_repo = SqlEmailJobRepository(session)
    campaign_repo = SqlCampaignRepository(session)
    warmup = WarmupEvaluator(SqlWarmupScheduleRepository(session))
    adaptive = AdaptiveThrottle(email_job_repo, SqlSenderCooldownRepository(session))
    throttle = ThrottleEngine(
        SqlSenderRepository(session),
        SqlProviderProfileRepository(session),
        SqlRateLimitRepository(session),
        email_job_repo,
        SqlCampaignSenderRepository(session),
        warmup,
        adaptive,
    )
    return SendEmailUseCase(
        email_job_repo,
        campaign_repo,
        SqlCampaignSenderRepository(session),
        SqlSenderRepository(session),
        SqlSequenceStepRepository(session),
        SqlRecipientStateRepository(session),
        SqlAttachmentRepository(session),
        throttle,
        mailer,
        cipher,
        NullStorage(),
        queue,
        CampaignCompletionChecker(campaign_repo, email_job_repo),
        "http://testserver",
    )


async def _seed_job(session, cipher, verified=True):
    now = datetime.now(timezone.utc)
    user = UserRow(
        id=uuid.uuid4().hex, google_id=uuid.uuid4().hex, email="u@example.com",
        name="U", avatar_url=None, created_at=now,
    )
    sender = SenderRow(
        id=uuid.uuid4().hex, user_id=user.id, email="s@example.com", name="S",
        app_password=cipher.encrypt("pw"), smtp_host="smtp.gmail.com", smtp_port=465,
        is_verified=verified, daily_limit=500, hourly_limit=None,
        provider_profile_id=None, created_at=now, updated_at=now,
    )
    campaign = EmailCampaignRow(
        id=uuid.uuid4().hex, user_id=user.id, sender_id=sender.id,
        subject="Hi {{name}}", body="<p>Hello {{name}}</p>", start_time=now,
        delay_seconds=0, hourly_limit=60, total_recipients=1, status="SCHEDULED",
        pause_reason=None, track_opens=True, track_clicks=True, created_at=now,
    )
    job = EmailJobRow(
        id=uuid.uuid4().hex, campaign_id=campaign.id, sender_id=sender.id,
        to_email="alice@example.com", scheduled_at=now, sent_at=None,
        status="PENDING", error=None, is_starred=False,
        column_data={"name": "Alice"}, is_replied=False, sequence_step_id=None,
        created_at=now, updated_at=now,
    )
    session.add_all([user, sender, campaign, job])
    await session.commit()
    return campaign.id, job.id


async def test_send_success_completes_campaign(session_factory):
    cipher = AesCredentialCipher(KEY)
    mailer = FakeMailer()
    queue = FakeQueue()
    async with session_factory() as session:
        campaign_id, job_id = await _seed_job(session, cipher)
        use_case = _build_use_case(session, mailer, queue, cipher)
        await use_case.execute(job_id)
        await session.commit()

    async with session_factory() as session:
        job = await SqlEmailJobRepository(session).get(job_id)
        campaign = await SqlCampaignRepository(session).get(campaign_id)

    assert job.status == "SENT"
    assert job.sent_at is not None
    assert campaign.status == "COMPLETED"
    assert len(mailer.sent) == 1
    sent = mailer.sent[0]
    assert sent["subject"] == "Hi Alice"
    assert "/track/open/" in sent["html_body"]
    assert sent["to_email"] == "alice@example.com"


async def test_send_failure_marks_failed(session_factory):
    cipher = AesCredentialCipher(KEY)
    mailer = FakeMailer(send_error=RuntimeError("550 mailbox unavailable"))
    queue = FakeQueue()
    async with session_factory() as session:
        campaign_id, job_id = await _seed_job(session, cipher)
        use_case = _build_use_case(session, mailer, queue, cipher)
        await use_case.execute(job_id)
        await session.commit()

    async with session_factory() as session:
        job = await SqlEmailJobRepository(session).get(job_id)
        campaign = await SqlCampaignRepository(session).get(campaign_id)
        cooldown = await SqlSenderCooldownRepository(session).get_by_sender(job.sender_id)

    assert job.status == "FAILED"
    assert "550" in job.error
    assert campaign.status == "COMPLETED"
    assert cooldown.consecutive_errors == 1


async def test_unverified_sender_fails_job(session_factory):
    cipher = AesCredentialCipher(KEY)
    mailer = FakeMailer()
    queue = FakeQueue()
    async with session_factory() as session:
        _, job_id = await _seed_job(session, cipher, verified=False)
        use_case = _build_use_case(session, mailer, queue, cipher)
        await use_case.execute(job_id)
        await session.commit()

    async with session_factory() as session:
        job = await SqlEmailJobRepository(session).get(job_id)

    assert job.status == "FAILED"
    assert job.error == "Sender not verified for SMTP"
    assert mailer.sent == []
