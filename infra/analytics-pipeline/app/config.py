from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except Exception:
        return default


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    service_name: str = "aos-analytics-pipeline"
    environment: str = "development"
    debug: bool = False

    request_secret: str = Field(default="", repr=False)
    callback_secret: str = Field(default="", repr=False)

    redis_url: str = "redis://analytics-redis:6379/0"
    queue_name: str = "analytics-pipeline"
    job_timeout_seconds: int = 600
    result_ttl_seconds: int = 86400
    failure_ttl_seconds: int = 604800
    durable_result_ttl_seconds: int = 604800
    callback_job_timeout_seconds: int = 120
    callback_max_attempts: int = 8

    stream_max_len: int = 500000
    max_events_per_job: int = 200
    event_dedupe_ttl_seconds: int = 2592000
    aggregate_retention_seconds: int = 34560000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        service_name=os.environ.get("ANALYTICS_SERVICE_NAME", "aos-analytics-pipeline"),
        environment=os.environ.get("ANALYTICS_SERVICE_ENVIRONMENT", "development"),
        debug=_bool("ANALYTICS_SERVICE_DEBUG", False),
        request_secret=os.environ.get("ANALYTICS_SERVICE_SECRET", ""),
        callback_secret=os.environ.get("ANALYTICS_SERVICE_CALLBACK_SECRET", ""),
        redis_url=os.environ.get("ANALYTICS_REDIS_URL", "redis://analytics-redis:6379/0"),
        queue_name=os.environ.get("ANALYTICS_QUEUE_NAME", "analytics-pipeline"),
        job_timeout_seconds=_int("ANALYTICS_JOB_TIMEOUT_SECONDS", 600),
        result_ttl_seconds=_int("ANALYTICS_RESULT_TTL_SECONDS", 86400),
        failure_ttl_seconds=_int("ANALYTICS_FAILURE_TTL_SECONDS", 604800),
        durable_result_ttl_seconds=_int("ANALYTICS_DURABLE_RESULT_TTL_SECONDS", 604800),
        callback_job_timeout_seconds=_int("ANALYTICS_CALLBACK_JOB_TIMEOUT_SECONDS", 120),
        callback_max_attempts=_int("ANALYTICS_CALLBACK_MAX_ATTEMPTS", 8),
        stream_max_len=_int("ANALYTICS_STREAM_MAX_LEN", 500000),
        max_events_per_job=_int("ANALYTICS_MAX_EVENTS_PER_JOB", 200),
        event_dedupe_ttl_seconds=_int("ANALYTICS_EVENT_DEDUPE_TTL_SECONDS", 2592000),
        aggregate_retention_seconds=_int("ANALYTICS_AGGREGATE_RETENTION_SECONDS", 34560000),
    )
