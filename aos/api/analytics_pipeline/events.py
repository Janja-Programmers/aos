from __future__ import annotations

import json
from typing import Any

import frappe

from aos.api.shared.auth import current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.services.analytics_pipeline_service import create_analytics_ingest_job

TRACK_EVENT_LIMIT_PER_MINUTE_PER_IP = 120
TRACK_EVENTS_LIMIT_PER_MINUTE_PER_IP = 60
MAX_BATCH_EVENTS = 100
MAX_EVENT_JSON_BYTES = 16 * 1024
MAX_EVENT_COLLECTION_ITEMS = 50
MAX_EVENT_STRING_LENGTH = 1000
MAX_EVENT_NESTING_DEPTH = 4
MAX_BATCH_JSON_BYTES = 512 * 1024


def _bounded_event_value(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_EVENT_NESTING_DEPTH:
        raise ValueError("Analytics event nesting is too deep.")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_EVENT_STRING_LENGTH]
    if isinstance(value, list):
        if len(value) > MAX_EVENT_COLLECTION_ITEMS:
            raise ValueError("Analytics event collection is too large.")
        return [_bounded_event_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > MAX_EVENT_COLLECTION_ITEMS:
            raise ValueError("Analytics event object is too large.")
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key or "").strip()[:120]
            if not key:
                continue
            result[key] = _bounded_event_value(item, depth=depth + 1)
        return result
    raise ValueError("Analytics event contains an unsupported value.")


def _prepare_public_event(raw: Any, *, user: str, source: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Analytics event must be an object.")

    event = dict(raw)
    event_type = event.get("event_type")
    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("event_type is required.")
    event["event_type"] = event_type.strip()[:120]

    for field in ("metadata", "metrics"):
        value = event.get(field)
        if value is None:
            event[field] = {}
        elif not isinstance(value, dict):
            raise ValueError(f"{field} must be an object.")
        else:
            event[field] = _bounded_event_value(value)

    # Client-supplied identity and source are telemetry inputs, not authority.
    # Authenticated identity is derived from the session; guest events are
    # explicitly anonymous.
    event["user"] = "" if user == "Guest" else user
    event["source"] = source

    encoded = json.dumps(event, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    if len(encoded) > MAX_EVENT_JSON_BYTES:
        raise ValueError("Analytics event is too large.")
    return event


def _extract_event(kwargs: dict[str, Any]) -> dict[str, Any]:
    raw = kwargs.get("event") if isinstance(kwargs.get("event"), dict) else kwargs
    return _prepare_public_event(
        raw,
        user=current_user(),
        source="api.analytics_pipeline.track_event",
    )


def track_event_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:analytics:track_event:ip:{request_ip()}",
        ttl_seconds=60,
        limit=TRACK_EVENT_LIMIT_PER_MINUTE_PER_IP,
        message="Too many analytics events. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        event = _extract_event(kwargs)
    except ValueError:
        return fail("Invalid analytics event.", error="VALIDATION_ERROR")

    try:
        job = create_analytics_ingest_job(
            events=[event],
            source=str(event.get("source") or "client").strip() or "client",
            enqueue=True,
        )
        return ok(
            "Analytics event queued.",
            data={"job_id": getattr(job, "name", None)},
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "track_event failed")
        return fail("Failed to queue analytics event", error="INTERNAL_ERROR")


def track_events_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:analytics:track_events:ip:{request_ip()}",
        ttl_seconds=60,
        limit=TRACK_EVENTS_LIMIT_PER_MINUTE_PER_IP,
        message="Too many analytics event batches. Please try again shortly.",
    )
    if rl:
        return rl

    events = kwargs.get("events")
    if not isinstance(events, list) or not events:
        return fail("events must be a non-empty list.", error="VALIDATION_ERROR")
    if len(events) > MAX_BATCH_EVENTS:
        return fail(
            f"A maximum of {MAX_BATCH_EVENTS} events may be submitted at once.",
            error="VALIDATION_ERROR",
        )

    user = current_user()
    normalized = []
    try:
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("Each analytics event must be an object.")
            normalized.append(
                _prepare_public_event(
                    event,
                    user=user,
                    source="api.analytics_pipeline.track_events",
                )
            )
        batch_bytes = len(
            json.dumps(normalized, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
        )
        if batch_bytes > MAX_BATCH_JSON_BYTES:
            raise ValueError("Analytics event batch is too large.")
    except ValueError:
        return fail("Invalid analytics event batch.", error="VALIDATION_ERROR")

    if not normalized:
        return fail("No valid events supplied.", error="VALIDATION_ERROR")

    try:
        job = create_analytics_ingest_job(
            events=normalized,
            source="client_batch",
            enqueue=True,
        )
        return ok(
            "Analytics events queued.",
            data={"job_id": getattr(job, "name", None), "event_count": len(normalized)},
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "track_events failed")
        return fail("Failed to queue analytics events", error="INTERNAL_ERROR")
