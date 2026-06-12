import uuid
from datetime import timedelta

from arq.connections import ArqRedis, RedisSettings, create_pool

SEND_EMAIL_JOB = "send_email"


class ArqEmailQueue:
    def __init__(self, redis: ArqRedis):
        self._redis = redis

    async def enqueue_send(self, email_job_id: str, delay_ms: int = 0) -> None:
        await self._redis.enqueue_job(
            SEND_EMAIL_JOB,
            email_job_id,
            _job_id=f"{email_job_id}-{uuid.uuid4().hex}",
            _defer_by=timedelta(milliseconds=delay_ms) if delay_ms > 0 else None,
        )


async def create_email_queue(redis_url: str) -> ArqEmailQueue:
    redis = await create_pool(RedisSettings.from_dsn(redis_url))
    return ArqEmailQueue(redis)
