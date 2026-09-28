"""Canonical cross-feature Analytics event taxonomy.

Only server-authoritative events that need the shared analytics pipeline belong
here. Client-observed telemetry stays behind the owning feature's validated
endpoint (for example Shorts ``record_events``), where visibility and semantic
rules can be enforced against canonical business state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from aos.services.accounts.identity import normalize_public_account_id

_AD_ID_RE = re.compile(r"^AD-[A-Z0-9][A-Z0-9_-]{2,127}$", re.IGNORECASE)


@dataclass(frozen=True)
class AnalyticsEventSpec:
    event_group: str
    target_doctype: str
    route_type: str
    actor_optional: bool = True


SERVER_EVENT_SPECS: dict[str, AnalyticsEventSpec] = {
    "ad_detail_view": AnalyticsEventSpec(
        event_group="ads",
        target_doctype="AOS Ad",
        route_type="ad",
    ),
}


def canonicalize_server_event(event: dict[str, Any]) -> dict[str, Any]:
    """Validate and minimize one server-authoritative pipeline event."""
    if not isinstance(event, dict):
        raise ValueError("Analytics event must be an object")
    event_type = str(event.get("event_type") or "").strip()
    spec = SERVER_EVENT_SPECS.get(event_type)
    if spec is None:
        raise ValueError("Unknown server analytics event")

    allowed = {
        "event_id", "event_type", "actor_account_id", "target_name", "occurred_at",
        "source", "platform", "country", "metadata", "metrics",
    }
    unknown = set(event) - allowed
    if unknown:
        raise ValueError("Unknown analytics event fields")

    actor = normalize_public_account_id(event.get("actor_account_id"))
    if event.get("actor_account_id") and not actor:
        raise ValueError("Invalid analytics actor account id")

    target_name = str(event.get("target_name") or "").strip()
    if not _AD_ID_RE.fullmatch(target_name):
        raise ValueError("Invalid analytics target id")

    metadata = event.get("metadata") or {}
    metrics = event.get("metrics") or {}
    if not isinstance(metadata, dict) or not isinstance(metrics, dict):
        raise ValueError("Analytics metadata and metrics must be objects")

    return {
        "event_id": str(event.get("event_id") or "").strip()[:200],
        "event_type": event_type,
        "event_group": spec.event_group,
        "actor_account_id": actor,
        "source": str(event.get("source") or "server").strip()[:120] or "server",
        "platform": str(event.get("platform") or "").strip()[:40],
        "country": str(event.get("country") or "").strip()[:80],
        "target_doctype": spec.target_doctype,
        "target_name": target_name,
        "route_type": spec.route_type,
        "route_id": target_name,
        "occurred_at": str(event.get("occurred_at") or "").strip()[:80],
        "metadata": metadata,
        "metrics": metrics,
    }
