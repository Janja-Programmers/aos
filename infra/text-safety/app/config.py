from __future__ import annotations

import os
from dataclasses import dataclass, field


def _clean(name: str, default: str) -> str:
    value = str(os.getenv(name) or "").strip()
    return value or default


def _int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


@dataclass(frozen=True)
class Settings:
    service_name: str = _clean("TEXT_SAFETY_SERVICE_NAME", "aos-text-safety")
    environment: str = _clean("TEXT_SAFETY_ENVIRONMENT", "development")
    internal_secret: str = field(default=_clean("TEXT_SAFETY_INTERNAL_SECRET", ""), repr=False)
    model_path: str = _clean("TEXT_SAFETY_MODEL_PATH", "/models/text-safety")
    model_name: str = _clean("TEXT_SAFETY_MODEL_NAME", "multilingual-MiniLMv2-L6-mnli-xnli")
    model_revision: str = _clean("TEXT_SAFETY_MODEL_REVISION", "acf08db83390e23428c560cb578a865b39196993")
    model_version: str = _clean("TEXT_SAFETY_MODEL_VERSION", "minilm-nli-v3")
    max_items: int = _int("TEXT_SAFETY_MAX_ITEMS", 16, 1, 32)
    max_chars_per_item: int = _int("TEXT_SAFETY_MAX_CHARS_PER_ITEM", 6000, 128, 20000)
    max_tokens: int = _int("TEXT_SAFETY_MAX_TOKENS", 256, 64, 512)
    max_concurrent_requests: int = _int("TEXT_SAFETY_MAX_CONCURRENT_REQUESTS", 2, 1, 16)
    intra_op_threads: int = _int("TEXT_SAFETY_INTRA_OP_THREADS", 2, 1, 32)
    inter_op_threads: int = _int("TEXT_SAFETY_INTER_OP_THREADS", 1, 1, 8)
    # Transport/audit floor only. The inference provider must not own AOS policy thresholds.
    evidence_floor: float = _float("TEXT_SAFETY_EVIDENCE_FLOOR", 0.05, 0.0, 0.25)


def get_settings() -> Settings:
    return Settings()
