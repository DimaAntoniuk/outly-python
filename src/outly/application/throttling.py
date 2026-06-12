import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .ports import (
    CampaignSenderRepository,
    EmailJobRepository,
    ProviderProfileRepository,
    RateLimitRepository,
    SenderCooldownRepository,
    SenderRepository,
    WarmupScheduleRepository,
)

DEFAULT_WARMUP_DAILY_LIMITS = [20, 30, 50, 75, 100, 150, 200, 250, 300, 350, 400, 450, 475, 500]
DEFAULT_PROVIDER_LIMITS = {"per_minute": 10, "per_hour": 100, "per_day": 500}
ERROR_RATE_THRESHOLD = 0.1
BOUNCE_RATE_THRESHOLD = 0.05
CONSECUTIVE_ERROR_LIMIT = 3
BOUNCE_CODE_PATTERN = re.compile(r"5\d{2}")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def current_minute_window(now: datetime) -> datetime:
    return now.replace(second=0, microsecond=0)


def current_hour_window(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0)


def next_utc_midnight(now: datetime) -> datetime:
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def is_bounce_error(error: str | None) -> bool:
    if not error:
        return False
    return "bounce" in error.lower() or BOUNCE_CODE_PATTERN.search(error) is not None


@dataclass(frozen=True)
class AdaptiveState:
    error_rate: float
    bounce_rate: float
    consecutive_errors: int
    is_throttled: bool
    is_cooldown: bool
    cooldown_expires_at: datetime | None
    rate_multiplier: float


@dataclass(frozen=True)
class EffectiveLimits:
    per_minute: int
    per_hour: int
    per_day: int
    is_throttled: bool
    is_warmup: bool
    is_cooldown: bool
    cooldown_expires_at: datetime | None


@dataclass(frozen=True)
class ThrottleDecision:
    allowed: bool
    reason: str | None = None
    retry_after_ms: int | None = None


class WarmupEvaluator:
    def __init__(self, warmup_repo: WarmupScheduleRepository):
        self._warmup_repo = warmup_repo

    async def day_limit(self, sender_id: str, now: datetime | None = None) -> int | None:
        schedule = await self._warmup_repo.get_by_sender(sender_id)
        if schedule is None or schedule.opted_out or not schedule.is_active:
            return None
        now = now or utc_now()
        start = schedule.start_date
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        current_day = int((now - start).total_seconds() // 86400)
        if current_day < 0 or current_day >= schedule.duration_days:
            return None
        if current_day >= len(schedule.daily_limits):
            return None
        return schedule.daily_limits[current_day]

    async def is_in_warmup(self, sender_id: str) -> bool:
        return await self.day_limit(sender_id) is not None


class AdaptiveThrottle:
    def __init__(
        self,
        email_job_repo: EmailJobRepository,
        cooldown_repo: SenderCooldownRepository,
        cooldown_duration_ms: int = 300_000,
    ):
        self._email_job_repo = email_job_repo
        self._cooldown_repo = cooldown_repo
        self._cooldown_duration_ms = cooldown_duration_ms

    async def get_state(self, sender_id: str, now: datetime | None = None) -> AdaptiveState:
        now = now or utc_now()
        jobs = await self._email_job_repo.recent_outcomes(sender_id, now - timedelta(hours=1))
        total = len(jobs)
        failed = [job for job in jobs if job.status == "FAILED"]
        bounced = [job for job in failed if is_bounce_error(job.error)]
        error_rate = len(failed) / total if total else 0.0
        bounce_rate = len(bounced) / total if total else 0.0

        cooldown = await self._cooldown_repo.get_by_sender(sender_id)
        cooldown_until = cooldown.cooldown_until if cooldown else None
        if cooldown_until and cooldown_until.tzinfo is None:
            cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
        is_cooldown = cooldown_until is not None and cooldown_until > now

        is_throttled = error_rate > ERROR_RATE_THRESHOLD or bounce_rate > BOUNCE_RATE_THRESHOLD
        return AdaptiveState(
            error_rate=error_rate,
            bounce_rate=bounce_rate,
            consecutive_errors=cooldown.consecutive_errors if cooldown else 0,
            is_throttled=is_throttled,
            is_cooldown=is_cooldown,
            cooldown_expires_at=cooldown_until if is_cooldown else None,
            rate_multiplier=0.5 if is_throttled else 1.0,
        )

    async def record_error(self, sender_id: str) -> None:
        existing = await self._cooldown_repo.get_by_sender(sender_id)
        new_count = (existing.consecutive_errors if existing else 0) + 1
        cooldown_until = existing.cooldown_until if existing else None
        if new_count >= CONSECUTIVE_ERROR_LIMIT:
            cooldown_until = utc_now() + timedelta(milliseconds=self._cooldown_duration_ms)
        await self._cooldown_repo.upsert(sender_id, new_count, cooldown_until)

    async def reset_errors(self, sender_id: str) -> None:
        await self._cooldown_repo.upsert(sender_id, 0, None)


class ThrottleEngine:
    def __init__(
        self,
        sender_repo: SenderRepository,
        provider_repo: ProviderProfileRepository,
        rate_limit_repo: RateLimitRepository,
        email_job_repo: EmailJobRepository,
        campaign_sender_repo: CampaignSenderRepository,
        warmup: WarmupEvaluator,
        adaptive: AdaptiveThrottle,
    ):
        self._sender_repo = sender_repo
        self._provider_repo = provider_repo
        self._rate_limit_repo = rate_limit_repo
        self._email_job_repo = email_job_repo
        self._campaign_sender_repo = campaign_sender_repo
        self._warmup = warmup
        self._adaptive = adaptive

    async def provider_limits(self, sender_id: str) -> dict[str, int]:
        sender = await self._sender_repo.get(sender_id)
        if sender is None or sender.provider_profile_id is None:
            return dict(DEFAULT_PROVIDER_LIMITS)
        profile = await self._provider_repo.get(sender.provider_profile_id)
        if profile is None:
            return dict(DEFAULT_PROVIDER_LIMITS)
        return {
            "per_minute": profile.per_minute_limit,
            "per_hour": profile.per_hour_limit,
            "per_day": profile.per_day_limit,
        }

    async def effective_limits(self, sender_id: str) -> EffectiveLimits:
        provider = await self.provider_limits(sender_id)
        sender = await self._sender_repo.get(sender_id)
        warmup_limit = await self._warmup.day_limit(sender_id)
        adaptive = await self._adaptive.get_state(sender_id)

        sender_hourly = sender.hourly_limit if sender else None
        sender_daily = sender.daily_limit if sender else None
        per_minute = max(1, math.floor(provider["per_minute"] * adaptive.rate_multiplier))
        per_hour = max(
            1,
            math.floor(
                min(provider["per_hour"], sender_hourly or provider["per_hour"])
                * adaptive.rate_multiplier
            ),
        )
        day_candidates = [provider["per_day"], sender_daily or provider["per_day"]]
        if warmup_limit is not None:
            day_candidates.append(warmup_limit)
        per_day = max(1, math.floor(min(day_candidates) * adaptive.rate_multiplier))
        return EffectiveLimits(
            per_minute=per_minute,
            per_hour=per_hour,
            per_day=per_day,
            is_throttled=adaptive.is_throttled,
            is_warmup=warmup_limit is not None,
            is_cooldown=adaptive.is_cooldown,
            cooldown_expires_at=adaptive.cooldown_expires_at,
        )

    async def sent_count_today(self, sender_id: str) -> int:
        return await self._email_job_repo.sent_count_today(sender_id, utc_now())

    async def has_daily_capacity(self, sender_id: str, daily_limit: int) -> bool:
        return await self.sent_count_today(sender_id) < daily_limit

    async def can_send(
        self, sender_id: str, campaign_hourly_limit: int | None = None
    ) -> ThrottleDecision:
        now = utc_now()
        limits = await self.effective_limits(sender_id)
        effective_hourly = min(limits.per_hour, campaign_hourly_limit or limits.per_hour)

        if limits.is_cooldown and limits.cooldown_expires_at:
            retry = int((limits.cooldown_expires_at - now).total_seconds() * 1000)
            return ThrottleDecision(False, "cooldown", max(0, retry))

        minute_count = await self._rate_limit_repo.minute_count(
            sender_id, current_minute_window(now)
        )
        if minute_count >= limits.per_minute:
            return ThrottleDecision(False, "rate-limited-minute", 60_000)

        hour_count = await self._rate_limit_repo.hour_count(sender_id, current_hour_window(now))
        if hour_count >= effective_hourly:
            return ThrottleDecision(False, "rate-limited-hour", 3_600_000)

        if await self.sent_count_today(sender_id) >= limits.per_day:
            retry = int((next_utc_midnight(now) - now).total_seconds() * 1000)
            return ThrottleDecision(False, "daily-limit", retry)

        return ThrottleDecision(True)

    async def record_send_result(self, sender_id: str, success: bool) -> None:
        now = utc_now()
        await self._rate_limit_repo.increment(
            sender_id, current_minute_window(now), current_hour_window(now)
        )
        if success:
            await self._adaptive.reset_errors(sender_id)
        else:
            await self._adaptive.record_error(sender_id)

    async def find_available_sender(self, campaign_id: str) -> str | None:
        pool = await self._campaign_sender_repo.list_for_campaign(campaign_id)
        for campaign_sender in sorted(pool, key=lambda cs: cs.rotation_order):
            limits = await self.effective_limits(campaign_sender.sender_id)
            if await self.has_daily_capacity(campaign_sender.sender_id, limits.per_day):
                return campaign_sender.sender_id
        return None

    async def earliest_resume_time(self, campaign_id: str) -> datetime:
        now = utc_now()
        pool = await self._campaign_sender_repo.list_for_campaign(campaign_id)
        earliest = now + timedelta(hours=24)
        for campaign_sender in pool:
            sender_id = campaign_sender.sender_id
            limits = await self.effective_limits(sender_id)
            if limits.is_cooldown and limits.cooldown_expires_at:
                earliest = min(earliest, limits.cooldown_expires_at)
                continue
            if await self.sent_count_today(sender_id) >= limits.per_day:
                earliest = min(earliest, next_utc_midnight(now))
                continue
            hour_count = await self._rate_limit_repo.hour_count(
                sender_id, current_hour_window(now)
            )
            if hour_count >= limits.per_hour:
                earliest = min(earliest, current_hour_window(now) + timedelta(hours=1))
                continue
            minute_count = await self._rate_limit_repo.minute_count(
                sender_id, current_minute_window(now)
            )
            if minute_count >= limits.per_minute:
                earliest = min(earliest, current_minute_window(now) + timedelta(minutes=1))
                continue
            return now
        return earliest
