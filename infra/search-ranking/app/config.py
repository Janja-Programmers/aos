from __future__ import annotations

import os
from dataclasses import dataclass, field


def _clean(value: object | None, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = int(default)
    if min_value is not None:
        value = max(value, min_value)
    if max_value is not None:
        value = min(value, max_value)
    return value


def _float(name: str, default: float, *, min_value: float | None = None, max_value: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = float(default)
    if min_value is not None:
        value = max(value, min_value)
    if max_value is not None:
        value = min(value, max_value)
    return value


@dataclass(frozen=True)
class Settings:
    service_name: str = _clean(os.getenv("SEARCH_RANKING_SERVICE_NAME"), "aos-search-ranking")
    environment: str = _clean(os.getenv("SEARCH_RANKING_SERVICE_ENVIRONMENT"), "development")
    debug: bool = _bool("SEARCH_RANKING_SERVICE_DEBUG", False)

    redis_url: str = _clean(os.getenv("SEARCH_RANKING_REDIS_URL"), "redis://search-ranking-redis:6379/0")
    queue_name: str = _clean(os.getenv("SEARCH_RANKING_QUEUE_NAME"), "search-ranking")
    job_timeout_seconds: int = _int("SEARCH_RANKING_JOB_TIMEOUT_SECONDS", 600, min_value=60)
    result_ttl_seconds: int = _int("SEARCH_RANKING_RESULT_TTL_SECONDS", 86400, min_value=60)
    failure_ttl_seconds: int = _int("SEARCH_RANKING_FAILURE_TTL_SECONDS", 604800, min_value=60)
    durable_result_ttl_seconds: int = _int("SEARCH_RANKING_DURABLE_RESULT_TTL_SECONDS", 604800, min_value=3600)
    callback_job_timeout_seconds: int = _int("SEARCH_RANKING_CALLBACK_JOB_TIMEOUT_SECONDS", 120, min_value=30, max_value=900)
    callback_max_attempts: int = _int("SEARCH_RANKING_CALLBACK_MAX_ATTEMPTS", 8, min_value=1, max_value=20)

    request_secret: str = field(default=_clean(os.getenv("SEARCH_RANKING_SERVICE_SECRET"), ""), repr=False)
    callback_secret: str = field(default=_clean(os.getenv("SEARCH_RANKING_SERVICE_CALLBACK_SECRET"), ""), repr=False)

    max_ad_candidates: int = _int("SEARCH_RANKING_MAX_AD_CANDIDATES", 500, min_value=10, max_value=5000)
    max_short_candidates: int = _int("SEARCH_RANKING_MAX_SHORT_CANDIDATES", 500, min_value=10, max_value=5000)
    text_match_weight: float = _float("SEARCH_RANKING_TEXT_MATCH_WEIGHT", 100.0, min_value=1.0)
    recency_weight: float = _float("SEARCH_RANKING_RECENCY_WEIGHT", 1.0, min_value=0.0)


def get_settings() -> Settings:
    return Settings()
