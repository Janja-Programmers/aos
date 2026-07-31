"""Analytics event keys and bounds."""

from __future__ import annotations

import hashlib


def event_key(*, event_type: str, short_id: str, actor_key: str, client_event_id: str | None = None) -> str:
    material = "|".join(
        [str(event_type or ""), str(short_id or ""), str(actor_key or ""), str(client_event_id or "")]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def bounded_watch_ms(value: object, *, duration_seconds: object) -> int:
    watch_ms = max(0, int(value or 0))
    duration_ms = max(0, int(float(duration_seconds or 0) * 1000))
    if duration_ms:
        return min(watch_ms, duration_ms + 5_000)
    return min(watch_ms, 3_605_000)
