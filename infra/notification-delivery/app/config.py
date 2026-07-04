from __future__ import annotations

import os
from functools import lru_cache

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

    service_name: str = "aos-notification-delivery"
    environment: str = "development"
    debug: bool = False

    request_secret: str = ""
    callback_secret: str = ""

    redis_url: str = "redis://notification-redis:6379/0"
    queue_name: str = "notification-delivery"
    job_timeout_seconds: int = 600
    result_ttl_seconds: int = 86400
    failure_ttl_seconds: int = 604800

    dry_run: bool = False
    firebase_service_account_path: str = "/run/secrets/firebase-service-account.json"
    max_tokens_per_multicast: int = 500


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        service_name=os.environ.get("NOTIFICATION_SERVICE_NAME", "aos-notification-delivery"),
        environment=os.environ.get("NOTIFICATION_SERVICE_ENVIRONMENT", "development"),
        debug=_bool("NOTIFICATION_SERVICE_DEBUG", False),
        request_secret=os.environ.get("NOTIFICATION_SERVICE_SECRET", ""),
        callback_secret=os.environ.get("NOTIFICATION_SERVICE_CALLBACK_SECRET", ""),
        redis_url=os.environ.get("NOTIFICATION_REDIS_URL", "redis://notification-redis:6379/0"),
        queue_name=os.environ.get("NOTIFICATION_QUEUE_NAME", "notification-delivery"),
        job_timeout_seconds=_int("NOTIFICATION_JOB_TIMEOUT_SECONDS", 600),
        result_ttl_seconds=_int("NOTIFICATION_RESULT_TTL_SECONDS", 86400),
        failure_ttl_seconds=_int("NOTIFICATION_FAILURE_TTL_SECONDS", 604800),
        dry_run=_bool("NOTIFICATION_DRY_RUN", False),
        firebase_service_account_path=os.environ.get(
            "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH",
            "/run/secrets/firebase-service-account.json",
        ),
        max_tokens_per_multicast=_int("NOTIFICATION_MAX_TOKENS_PER_MULTICAST", 500),
    )
