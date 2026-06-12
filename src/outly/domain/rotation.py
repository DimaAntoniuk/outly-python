from dataclasses import dataclass


@dataclass(frozen=True)
class PoolSender:
    sender_id: str
    rotation_order: int
    daily_limit: int | None = None


def assign_senders_round_robin(
    senders: list[PoolSender], email_count: int
) -> list[str | None]:
    if not senders or email_count <= 0:
        return []
    ordered = sorted(senders, key=lambda sender: sender.rotation_order)
    if all(sender.daily_limit is None for sender in ordered):
        return [ordered[index % len(ordered)].sender_id for index in range(email_count)]

    assigned: dict[str, int] = {sender.sender_id: 0 for sender in ordered}
    result: list[str | None] = []
    cursor = 0
    for _ in range(email_count):
        chosen: PoolSender | None = None
        for attempt in range(len(ordered)):
            candidate = ordered[(cursor + attempt) % len(ordered)]
            limit = candidate.daily_limit
            if limit is None or assigned[candidate.sender_id] < limit:
                chosen = candidate
                cursor = (cursor + attempt + 1) % len(ordered)
                break
        if chosen is None:
            break
        assigned[chosen.sender_id] += 1
        result.append(chosen.sender_id)
    return result


def compute_combined_daily_limit(senders: list[PoolSender]) -> int:
    return sum(sender.daily_limit if sender.daily_limit is not None else 500 for sender in senders)
