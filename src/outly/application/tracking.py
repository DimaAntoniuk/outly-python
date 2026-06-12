import base64
import uuid
from typing import Any

from ..domain.entities import TrackingEvent
from ..domain.enums import TrackingEventType
from .errors import Forbidden, NotFound
from .ports import CampaignRepository, EmailJobRepository, TrackingEventRepository
from .throttling import utc_now

TRANSPARENT_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


class TrackingService:
    def __init__(
        self,
        tracking_repo: TrackingEventRepository,
        email_job_repo: EmailJobRepository,
        campaign_repo: CampaignRepository,
    ):
        self._tracking_repo = tracking_repo
        self._email_job_repo = email_job_repo
        self._campaign_repo = campaign_repo

    async def record_open(
        self, email_job_id: str, ip_address: str | None, user_agent: str | None
    ) -> None:
        try:
            if await self._email_job_repo.get(email_job_id) is None:
                return
            await self._tracking_repo.create(
                TrackingEvent(
                    id=uuid.uuid4().hex,
                    email_job_id=email_job_id,
                    event_type=TrackingEventType.OPEN,
                    url=None,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    created_at=utc_now(),
                )
            )
        except Exception:
            pass

    async def record_click(
        self, email_job_id: str, url: str, ip_address: str | None, user_agent: str | None
    ) -> None:
        try:
            if await self._email_job_repo.get(email_job_id) is None:
                return
            await self._tracking_repo.create(
                TrackingEvent(
                    id=uuid.uuid4().hex,
                    email_job_id=email_job_id,
                    event_type=TrackingEventType.CLICK,
                    url=url,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    created_at=utc_now(),
                )
            )
        except Exception:
            pass

    async def _verify_owner(self, user_id: str, campaign_id: str):
        campaign = await self._campaign_repo.get(campaign_id)
        if campaign is None:
            raise NotFound("Campaign not found")
        if campaign.user_id != user_id:
            raise Forbidden("Forbidden")
        return campaign

    async def campaign_metrics(self, user_id: str, campaign_id: str) -> dict[str, Any]:
        campaign = await self._verify_owner(user_id, campaign_id)
        counts = await self._email_job_repo.status_counts(campaign_id)
        total_sent = counts.get("SENT", 0)
        unique_opens = await self._tracking_repo.unique_event_count(
            campaign_id, TrackingEventType.OPEN
        )
        unique_clicks = await self._tracking_repo.unique_event_count(
            campaign_id, TrackingEventType.CLICK
        )
        open_rate = round(unique_opens / total_sent * 1000) / 10 if total_sent else 0
        click_rate = round(unique_clicks / total_sent * 1000) / 10 if total_sent else 0
        return {
            "campaignId": campaign_id,
            "totalSent": total_sent,
            "uniqueOpens": unique_opens,
            "uniqueClicks": unique_clicks,
            "openRate": open_rate,
            "clickRate": click_rate,
            "trackOpens": campaign.track_opens,
            "trackClicks": campaign.track_clicks,
        }

    async def campaign_emails(self, user_id: str, campaign_id: str) -> dict[str, Any]:
        await self._verify_owner(user_id, campaign_id)
        events_by_job = await self._tracking_repo.list_for_campaign_jobs(campaign_id)
        jobs = await self._email_job_repo.list_for_campaign(campaign_id)
        sent_jobs = sorted(
            (job for job in jobs if job.status == "SENT"),
            key=lambda job: job.sent_at or job.created_at,
            reverse=True,
        )
        emails = []
        for job in sent_jobs:
            events = sorted(
                events_by_job.get(job.id, []), key=lambda event: event.created_at, reverse=True
            )
            opens = [event for event in events if event.event_type == TrackingEventType.OPEN]
            clicks = [event for event in events if event.event_type == TrackingEventType.CLICK]
            emails.append(
                {
                    "emailJobId": job.id,
                    "toEmail": job.to_email,
                    "openCount": len(opens),
                    "clickCount": len(clicks),
                    "lastOpenAt": opens[0].created_at.isoformat() if opens else None,
                    "lastClickAt": clicks[0].created_at.isoformat() if clicks else None,
                }
            )
        return {"emails": emails}

    async def campaign_links(self, user_id: str, campaign_id: str) -> dict[str, Any]:
        await self._verify_owner(user_id, campaign_id)
        urls = await self._tracking_repo.list_click_urls(campaign_id)
        counts: dict[str, int] = {}
        for url in urls:
            counts[url] = counts.get(url, 0) + 1
        links = [
            {"url": url, "clickCount": count}
            for url, count in sorted(counts.items(), key=lambda item: -item[1])
        ]
        return {"links": links}
