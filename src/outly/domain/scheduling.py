import random
from datetime import datetime, timedelta


def compute_jittered_delay(base_delay_seconds: float) -> float:
    return max(1.0, base_delay_seconds * random.uniform(0.7, 1.3))


def compute_send_offsets(
    recipient_count: int, delay_seconds: int, hourly_limit: int, sender_count: int
) -> list[float]:
    combined_hourly = max(1, hourly_limit * max(1, sender_count))
    avg_gap = 3600 / combined_hourly
    offsets: list[float] = []
    cumulative = 0.0
    for index in range(recipient_count):
        if index > 0:
            if delay_seconds > avg_gap:
                gap = delay_seconds * random.uniform(0.8, 1.2)
            else:
                gap = max(float(delay_seconds), avg_gap * random.uniform(0.6, 1.4))
            cumulative += gap
        offsets.append(cumulative)
    return offsets


def schedule_times(
    start_time: datetime,
    recipient_count: int,
    delay_seconds: int,
    hourly_limit: int,
    sender_count: int,
) -> list[datetime]:
    offsets = compute_send_offsets(recipient_count, delay_seconds, hourly_limit, sender_count)
    return [start_time + timedelta(seconds=offset) for offset in offsets]
