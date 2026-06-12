from typing import Any

from ..domain.entities import RecipientSequenceState
from ..domain.enums import StepStatus
from .errors import Forbidden, NotFound
from .ports import CampaignRepository, RecipientStateRepository, SequenceStepRepository
from .throttling import utc_now


def _skip_unfinished_steps(state: RecipientSequenceState) -> None:
    for status in state.step_statuses:
        if status.get("status") not in (StepStatus.SENT, StepStatus.FAILED):
            status["status"] = StepStatus.SKIPPED


class SequenceService:
    def __init__(
        self,
        campaign_repo: CampaignRepository,
        sequence_step_repo: SequenceStepRepository,
        recipient_state_repo: RecipientStateRepository,
    ):
        self._campaign_repo = campaign_repo
        self._sequence_step_repo = sequence_step_repo
        self._recipient_state_repo = recipient_state_repo

    async def _verify_owner(self, user_id: str, campaign_id: str) -> None:
        campaign = await self._campaign_repo.get(campaign_id)
        if campaign is None:
            raise NotFound("Campaign not found")
        if campaign.user_id != user_id:
            raise Forbidden("Forbidden")

    async def get_structure(self, user_id: str, campaign_id: str) -> dict[str, Any]:
        await self._verify_owner(user_id, campaign_id)
        steps = await self._sequence_step_repo.list_for_campaign(campaign_id)
        recipients = await self._recipient_state_repo.list_for_campaign(campaign_id)
        return {
            "steps": steps,
            "recipients": sorted(recipients, key=lambda state: state.recipient_email),
            "hasSequence": len(steps) > 0,
        }

    async def pause_all(self, user_id: str, campaign_id: str) -> int:
        await self._verify_owner(user_id, campaign_id)
        return await self._recipient_state_repo.set_paused_all(campaign_id, True)

    async def resume_all(self, user_id: str, campaign_id: str) -> int:
        await self._verify_owner(user_id, campaign_id)
        return await self._recipient_state_repo.set_paused_all(campaign_id, False)

    async def stop_all(self, user_id: str, campaign_id: str) -> int:
        await self._verify_owner(user_id, campaign_id)
        states = await self._recipient_state_repo.list_for_campaign(campaign_id)
        for state in states:
            _skip_unfinished_steps(state)
            state.completed = True
            state.updated_at = utc_now()
            await self._recipient_state_repo.update(state)
        return len(states)

    async def _get_recipient(
        self, user_id: str, campaign_id: str, recipient_id: str
    ) -> RecipientSequenceState:
        await self._verify_owner(user_id, campaign_id)
        state = await self._recipient_state_repo.get(recipient_id)
        if state is None or state.campaign_id != campaign_id:
            raise NotFound("Recipient not found")
        return state

    async def pause_recipient(
        self, user_id: str, campaign_id: str, recipient_id: str
    ) -> RecipientSequenceState:
        state = await self._get_recipient(user_id, campaign_id, recipient_id)
        state.paused = True
        state.updated_at = utc_now()
        return await self._recipient_state_repo.update(state)

    async def resume_recipient(
        self, user_id: str, campaign_id: str, recipient_id: str
    ) -> RecipientSequenceState:
        state = await self._get_recipient(user_id, campaign_id, recipient_id)
        state.paused = False
        state.updated_at = utc_now()
        return await self._recipient_state_repo.update(state)

    async def stop_recipient(
        self, user_id: str, campaign_id: str, recipient_id: str
    ) -> RecipientSequenceState:
        state = await self._get_recipient(user_id, campaign_id, recipient_id)
        _skip_unfinished_steps(state)
        state.completed = True
        state.updated_at = utc_now()
        return await self._recipient_state_repo.update(state)
