"""Calls-specific LiveKit identity and room-cleanup helpers."""

from __future__ import annotations

import time

import frappe

from aos.services.accounts.identity import public_account_id_for_user
from aos.services.live.livekit_admin import delete_room
from aos.services.livekit_service import LiveKitService

from .errors import CallError
from .observability import call_log

TERMINAL_STATUSES = ("ended", "missed", "rejected", "failed", "cancelled")
ROOM_CLEANUP_BATCH_SIZE = 20


def participant_identity(user: str) -> str:
    """Return the server-controlled public identity used inside call rooms."""
    identity = str(public_account_id_for_user(user) or "").strip()
    if not identity:
        raise CallError(
            "Account unavailable for calling.",
            code="CALL_ACCOUNT_UNAVAILABLE",
            http_status=403,
        )
    return identity


def issue_call_token(*, identity: str, room_name: str, metadata: str | None = None) -> str:
    """Mint a bounded Calls token and normalize configuration/signing failures."""
    started = time.monotonic()
    try:
        token = LiveKitService.generate_call_token(
            user=identity,
            room_name=room_name,
            metadata=metadata,
        )
        call_log(
            "token_issue",
            outcome="success",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return token
    except CallError:
        raise
    except Exception as exc:
        call_log(
            "token_issue",
            outcome="failure",
            reason="dependency",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise CallError(
            "Calling service is temporarily unavailable.",
            code="CALL_DEPENDENCY_UNAVAILABLE",
            http_status=503,
        ) from exc


def get_call_ws_url() -> str:
    try:
        return LiveKitService.get_ws_url()
    except Exception as exc:
        raise CallError(
            "Calling service is temporarily unavailable.",
            code="CALL_DEPENDENCY_UNAVAILABLE",
            http_status=503,
        ) from exc


def enqueue_room_cleanup(call_id: str) -> None:
    call_id = str(call_id or "").strip()
    if not call_id:
        return
    try:
        frappe.enqueue(
            "aos.tasks.calls.cleanup_call_room",
            queue="short",
            enqueue_after_commit=True,
            call_id=call_id,
        )
    except Exception:
        call_log("room_cleanup_enqueue", outcome="failure", reason="dependency")


def cleanup_room(call_id: str) -> dict[str, object]:
    row = frappe.db.get_value(
        "AOS Call",
        call_id,
        ["name", "room_name", "status", "room_cleanup_pending"],
        as_dict=True,
    )
    if not row:
        return {"ok": True, "outcome": "not_found"}
    if row.status not in TERMINAL_STATUSES:
        return {"ok": True, "outcome": "active"}
    if not int(row.room_cleanup_pending or 0):
        return {"ok": True, "outcome": "already_clean"}

    started = time.monotonic()
    result = delete_room(row.room_name)
    latency_ms = int((time.monotonic() - started) * 1000)
    if result.ok:
        frappe.db.set_value(
            "AOS Call",
            row.name,
            "room_cleanup_pending",
            0,
            update_modified=False,
        )
        call_log(
            "room_cleanup",
            outcome="success",
            reason=result.category,
            latency_ms=latency_ms,
        )
        return {"ok": True, "outcome": result.category}

    call_log(
        "room_cleanup",
        outcome="failure",
        reason=result.category,
        latency_ms=latency_ms,
    )
    return {"ok": False, "outcome": result.category}


def pending_cleanup_call_ids(limit: int = ROOM_CLEANUP_BATCH_SIZE) -> list[str]:
    bounded = max(1, min(int(limit or ROOM_CLEANUP_BATCH_SIZE), ROOM_CLEANUP_BATCH_SIZE))
    rows = frappe.get_all(
        "AOS Call",
        filters={
            "room_cleanup_pending": 1,
            "status": ["in", list(TERMINAL_STATUSES)],
        },
        pluck="name",
        order_by="modified asc, name asc",
        limit=bounded,
    )
    return [str(name) for name in rows if name]
