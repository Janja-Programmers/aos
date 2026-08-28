from __future__ import annotations

from redis import Redis
from rq import Queue

from app.config import get_settings


def get_redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url)


def get_queue() -> Queue:
    settings = get_settings()
    return Queue(settings.queue_name, connection=get_redis())


def get_callback_queue() -> Queue:
    settings = get_settings()
    return Queue(settings.callback_queue_name, connection=get_redis())
