import re
import uuid
from typing import Any

from ..domain.entities import Sender, WarmupSchedule
from .errors import BadRequest, Conflict, NotFound
from .ports import (
    CredentialCipher,
    Mailer,
    ProviderProfileRepository,
    RateLimitRepository,
    SenderRepository,
    WarmupScheduleRepository,
)
from .throttling import (
    DEFAULT_WARMUP_DAILY_LIMITS,
    AdaptiveThrottle,
    ThrottleEngine,
    WarmupEvaluator,
    current_hour_window,
    utc_now,
)

EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 465
WARMUP_DURATION_DAYS = 14


def sender_public(sender: Sender) -> dict[str, Any]:
    return {
        "id": sender.id,
        "userId": sender.user_id,
        "email": sender.email,
        "name": sender.name,
        "smtpHost": sender.smtp_host,
        "smtpPort": sender.smtp_port,
        "isVerified": sender.is_verified,
        "dailyLimit": sender.daily_limit,
        "createdAt": sender.created_at,
        "updatedAt": sender.updated_at,
    }


class SenderService:
    def __init__(
        self,
        sender_repo: SenderRepository,
        provider_repo: ProviderProfileRepository,
        warmup_repo: WarmupScheduleRepository,
        rate_limit_repo: RateLimitRepository,
        mailer: Mailer,
        cipher: CredentialCipher,
        throttle: ThrottleEngine,
        warmup: WarmupEvaluator,
        adaptive: AdaptiveThrottle,
    ):
        self._sender_repo = sender_repo
        self._provider_repo = provider_repo
        self._warmup_repo = warmup_repo
        self._rate_limit_repo = rate_limit_repo
        self._mailer = mailer
        self._cipher = cipher
        self._throttle = throttle
        self._warmup = warmup
        self._adaptive = adaptive

    async def _detect_provider_id(self, smtp_host: str) -> str | None:
        profile = await self._provider_repo.get_by_host(smtp_host)
        if profile is None:
            profile = await self._provider_repo.get_wildcard()
        return profile.id if profile else None

    async def _create_warmup(self, sender_id: str, opted_out: bool) -> None:
        if await self._warmup_repo.get_by_sender(sender_id) is not None:
            return
        await self._warmup_repo.create(
            WarmupSchedule(
                id=uuid.uuid4().hex,
                sender_id=sender_id,
                start_date=utc_now(),
                duration_days=WARMUP_DURATION_DAYS,
                daily_limits=list(DEFAULT_WARMUP_DAILY_LIMITS),
                is_active=True,
                opted_out=opted_out,
            )
        )

    async def create(
        self,
        user_id: str,
        name: str | None,
        email: str | None,
        app_password: str | None,
        skip_warmup: bool = False,
    ) -> dict[str, Any]:
        missing = [
            field
            for field, value in (("name", name), ("email", email), ("appPassword", app_password))
            if not value or not str(value).strip()
        ]
        if missing:
            raise BadRequest(f"Missing required fields: {', '.join(missing)}")
        assert name and email and app_password
        if not EMAIL_REGEX.match(email):
            raise BadRequest("Invalid email format")

        verified = await self._mailer.verify_credentials(
            DEFAULT_SMTP_HOST, DEFAULT_SMTP_PORT, email, app_password
        )
        if not verified:
            raise BadRequest(
                "Invalid SMTP credentials. Please check your email and app password."
            )

        existing = [s for s in await self._sender_repo.list_for_user(user_id) if s.email == email]
        if existing:
            raise Conflict("A sender with this email already exists for your account")

        now = utc_now()
        sender = Sender(
            id=uuid.uuid4().hex,
            user_id=user_id,
            email=email,
            name=name,
            app_password=self._cipher.encrypt(app_password),
            smtp_host=DEFAULT_SMTP_HOST,
            smtp_port=DEFAULT_SMTP_PORT,
            is_verified=True,
            daily_limit=500,
            hourly_limit=None,
            provider_profile_id=await self._detect_provider_id(DEFAULT_SMTP_HOST),
            created_at=now,
            updated_at=now,
        )
        sender = await self._sender_repo.create(sender)
        await self._create_warmup(sender.id, skip_warmup)
        return sender_public(sender)

    async def verify(
        self,
        user_id: str,
        sender_id: str,
        app_password: str | None,
        name: str | None = None,
        skip_warmup: bool = False,
    ) -> dict[str, Any]:
        if not app_password or not app_password.strip():
            raise BadRequest("App password is required")
        existing = await self._sender_repo.get_owned(sender_id, user_id)
        if existing is None:
            raise NotFound("Sender not found")

        verified = await self._mailer.verify_credentials(
            DEFAULT_SMTP_HOST, DEFAULT_SMTP_PORT, existing.email, app_password
        )
        if not verified:
            raise BadRequest("Invalid SMTP credentials. Please check your app password.")

        was_verified = existing.is_verified
        existing.app_password = self._cipher.encrypt(app_password)
        existing.is_verified = True
        if name:
            existing.name = name
        existing.provider_profile_id = await self._detect_provider_id(existing.smtp_host)
        updated = await self._sender_repo.update(existing)
        if not was_verified:
            await self._create_warmup(sender_id, skip_warmup)
        return sender_public(updated)

    async def list_with_stats(self, user_id: str) -> list[dict[str, Any]]:
        senders = await self._sender_repo.list_for_user(user_id)
        result = []
        for sender in senders:
            data = sender_public(sender)
            data["currentDailyCount"] = await self._throttle.sent_count_today(sender.id)
            result.append(data)
        return result

    async def list_emails(self, user_id: str) -> list[str]:
        return [sender.email for sender in await self._sender_repo.list_for_user(user_id)]

    async def get_detail(self, user_id: str, sender_id: str) -> dict[str, Any]:
        sender = await self._sender_repo.get_owned(sender_id, user_id)
        if sender is None:
            raise NotFound("Sender not found")

        hourly_count = await self._rate_limit_repo.hour_count(
            sender_id, current_hour_window(utc_now())
        )
        daily_count = await self._throttle.sent_count_today(sender_id)
        limits = await self._throttle.effective_limits(sender_id)
        adaptive = await self._adaptive.get_state(sender_id)
        warmup_status = await self.warmup_status(sender_id)

        data = sender_public(sender)
        data["hourlyLimit"] = sender.hourly_limit
        data.update(
            {
                "currentHourlyCount": hourly_count,
                "currentDailyCount": daily_count,
                "effectiveDailyLimit": limits.per_day,
                "warmupStatus": warmup_status,
                "cooldownState": {
                    "status": "active" if adaptive.is_cooldown else "inactive",
                    "expiresAt": adaptive.cooldown_expires_at,
                },
            }
        )
        return data

    async def warmup_status(self, sender_id: str) -> str:
        schedule = await self._warmup_repo.get_by_sender(sender_id)
        if schedule and schedule.opted_out:
            return "opted-out"
        if await self._warmup.is_in_warmup(sender_id):
            return "active"
        return "inactive"
