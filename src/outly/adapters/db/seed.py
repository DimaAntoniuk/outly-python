from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ProviderProfileRow

DEFAULT_PROFILES = [
    {"provider_name": "Gmail", "smtp_host_pattern": "smtp.gmail.com",
     "per_minute_limit": 10, "per_hour_limit": 100, "per_day_limit": 500},
    {"provider_name": "Outlook", "smtp_host_pattern": "smtp.office365.com",
     "per_minute_limit": 10, "per_hour_limit": 100, "per_day_limit": 300},
    {"provider_name": "Default", "smtp_host_pattern": "*",
     "per_minute_limit": 10, "per_hour_limit": 100, "per_day_limit": 500},
]


async def seed_provider_profiles(session: AsyncSession) -> None:
    for profile in DEFAULT_PROFILES:
        existing = await session.scalar(
            select(ProviderProfileRow).where(
                ProviderProfileRow.smtp_host_pattern == profile["smtp_host_pattern"]
            )
        )
        if existing is None:
            session.add(ProviderProfileRow(**profile))
    await session.commit()
