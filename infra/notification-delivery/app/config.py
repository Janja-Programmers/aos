from __future__ import annotations

import importlib.util
import json
import os
from functools import lru_cache
from pathlib import Path

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


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(_int(name, default), maximum))


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
    durable_result_ttl_seconds: int = 604800
    callback_job_timeout_seconds: int = 120
    callback_max_attempts: int = 8
    callback_http_timeout_seconds: int = 20

    dry_run: bool = False
    firebase_service_account_path: str = "/run/secrets/firebase-service-account.json"
    max_tokens_per_multicast: int = 500
    provider_max_retries: int = 3
    provider_timeout_seconds: int = 20


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
        job_timeout_seconds=_bounded_int("NOTIFICATION_JOB_TIMEOUT_SECONDS", 600, minimum=60, maximum=3600),
        result_ttl_seconds=_bounded_int("NOTIFICATION_RESULT_TTL_SECONDS", 86400, minimum=60, maximum=2592000),
        failure_ttl_seconds=_bounded_int(
            "NOTIFICATION_FAILURE_TTL_SECONDS", 604800, minimum=3600, maximum=2592000
        ),
        durable_result_ttl_seconds=_bounded_int(
            "NOTIFICATION_DURABLE_RESULT_TTL_SECONDS", 604800, minimum=3600, maximum=2592000
        ),
        callback_job_timeout_seconds=_bounded_int(
            "NOTIFICATION_CALLBACK_JOB_TIMEOUT_SECONDS", 120, minimum=30, maximum=900
        ),
        callback_max_attempts=_bounded_int(
            "NOTIFICATION_CALLBACK_MAX_ATTEMPTS", 8, minimum=1, maximum=20
        ),
        callback_http_timeout_seconds=_bounded_int(
            "NOTIFICATION_CALLBACK_HTTP_TIMEOUT_SECONDS", 20, minimum=3, maximum=120
        ),
        dry_run=_bool("NOTIFICATION_DRY_RUN", False),
        firebase_service_account_path=os.environ.get(
            "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH",
            "/run/secrets/firebase-service-account.json",
        ),
        max_tokens_per_multicast=_bounded_int(
            "NOTIFICATION_MAX_TOKENS_PER_MULTICAST", 500, minimum=1, maximum=500
        ),
        provider_max_retries=_bounded_int(
            "NOTIFICATION_PROVIDER_MAX_RETRIES",
            _int("NOTIFICATION_MAX_RETRIES", 3),
            minimum=1,
            maximum=10,
        ),
        provider_timeout_seconds=_bounded_int(
            "NOTIFICATION_PROVIDER_TIMEOUT_SECONDS", 20, minimum=5, maximum=120
        ),
    )


def validate_firebase_configuration(settings: Settings | None = None) -> None:
    """Validate local provider credentials without contacting Firebase."""
    settings = settings or get_settings()
    if settings.dry_run:
        return
    if importlib.util.find_spec("firebase_admin") is None:
        raise RuntimeError("Firebase provider library is unavailable")
    path = Path(settings.firebase_service_account_path)
    if not path.is_file():
        raise RuntimeError("Firebase service account is unavailable")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("Firebase service account is unreadable") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Firebase service account is invalid")
    required = ("project_id", "client_email", "private_key")
    if any(not str(payload.get(key) or "").strip() for key in required):
        raise RuntimeError("Firebase service account is incomplete")
