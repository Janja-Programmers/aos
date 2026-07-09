from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.auth import current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.services.analytics_pipeline_service import create_analytics_ingest_job

TRACK_EVENT_LIMIT_PER_MINUTE_PER_IP = 120
TRACK_EVENTS_LIMIT_PER_MINUTE_PER_IP = 60
MAX_BATCH_EVENTS = 100


def _extract_event(kwargs: dict[str, Any]) -> dict[str, Any]:
    event = dict(kwargs.get("event") or {}) if isinstance(kwargs.get("event"), dict) else dict(kwargs or {})
    user = current_user()
    if user != "Guest" and not event.get("user"):
        event["user"] = user
    event.setdefault("source", "api.analytics_pipeline.track_event")
    return event


def track_event_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:analytics:track_event:ip:{request_ip()}",
        ttl_seconds=60,
        limit=TRACK_EVENT_LIMIT_PER_MINUTE_PER_IP,
        message="Too many analytics events. Please try again shortly.",
    )
    if rl:
        return rl

    event = _extract_event(kwargs)
    if not str(event.get("event_type") or "").strip():
        return fail("event_type is required.", error="VALIDATION_ERROR")

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

    user = current_user()
    normalized = []
    for event in events[:MAX_BATCH_EVENTS]:
        if not isinstance(event, dict):
            continue
        item = dict(event)
        if user != "Guest" and not item.get("user"):
            item["user"] = user
        item.setdefault("source", "api.analytics_pipeline.track_events")
        normalized.append(item)

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
