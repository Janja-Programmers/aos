"""Analytics event keys and bounds."""

from __future__ import annotations

import hashlib


def event_key(*, event_type: str, short_id: str, actor_key: str, client_event_id: str | None = None) -> str:
    material = "|".join(
        [str(event_type or ""), str(short_id or ""), str(actor_key or ""), str(client_event_id or "")]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def short_view_identity_key(short_id: object, *, user: object = None, session_id: object = None) -> str:
    """Return the canonical globally-unique viewer identity for one Short.

    Keep this helper shared by event ingestion and ``AOS Short View.validate`` so
    concurrency recovery always queries the exact key that the DocType writes.
    """
    short = str(short_id or "").strip()
    viewer = str(user or "").strip()
    session = str(session_id or "").strip()
    if not short:
        raise ValueError("short_id is required")
    if viewer:
        actor_key = f"user:{viewer}"
    elif session:
        actor_key = f"session:{session}"
    else:
        raise ValueError("user or session_id is required")
    return hashlib.sha256(f"{short}|{actor_key}".encode("utf-8")).hexdigest()


def bounded_watch_ms(value: object, *, duration_seconds: object) -> int:
    watch_ms = max(0, int(value or 0))
    duration_ms = max(0, int(float(duration_seconds or 0) * 1000))
    if duration_ms:
        return min(watch_ms, duration_ms + 5_000)
    return min(watch_ms, 3_605_000)


def qualified_view_threshold_ms(*, duration_seconds: object) -> int:
    """Return the canonical qualified-view threshold for a Short.

    A view qualifies after 2 seconds, or after half of a shorter Short.  The
    server owns this rule; clients may suggest a qualified_view event but may
    not promote an under-threshold watch into a counted view.
    """
    duration_ms = max(0, int(float(duration_seconds or 0) * 1000))
    if duration_ms:
        return max(1, min(2_000, (duration_ms + 1) // 2))
    return 2_000


def qualifies_view(watch_ms: object, *, duration_seconds: object) -> bool:
    try:
        watched = max(0, int(watch_ms or 0))
    except (TypeError, ValueError):
        return False
    return watched >= qualified_view_threshold_ms(duration_seconds=duration_seconds)
