"""Calls-specific LiveKit integration on top of the shared RTC services."""

from __future__ import annotations

import time

import frappe

from aos.services.accounts.identity import public_account_id_for_user
from aos.services.livekit.admin import delete_room
from aos.services.livekit.admin import RoomAdminResult, ensure_room
from aos.services.livekit_service import LiveKitService

from .errors import CallError
from .observability import call_log

TERMINAL_STATUSES = ("ended", "missed", "rejected", "failed", "cancelled")
ROOM_CLEANUP_BATCH_SIZE = 20
DIRECT_CALL_MAX_PARTICIPANTS = 2
GROUP_CALL_MAX_PARTICIPANTS = 32
CALL_ROOM_MAX_PARTICIPANTS = GROUP_CALL_MAX_PARTICIPANTS


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


def call_rtc_ready(call) -> bool:
    """Return whether the shared RTC room is durably provisioned and active."""
    return bool(
        getattr(call, "rtc_provisioned_at", None)
        and int(getattr(call, "is_active", 0) or 0) == 1
        and str(getattr(call, "status", "") or "") in {"initiated", "ringing", "ongoing"}
    )

def ensure_call_join_ready(call) -> None:
    if call_rtc_ready(call):
        return
    raise CallError(
        "Call is still connecting.",
        code="CALL_NOT_READY",
        http_status=409,
    )


def issue_call_token(
    *,
    identity: str,
    room_name: str,
    call_type: str,
    metadata: str | None = None,
) -> str:
    """Mint a short-lived, call-type-scoped token and normalize failures."""
    started = time.monotonic()
    try:
        token = LiveKitService.generate_call_token(
            user=identity,
            room_name=room_name,
            metadata=metadata,
            call_type=call_type,
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



def room_capacity_for_call(call_mode: str) -> int:
    mode = str(call_mode or "").strip().lower()
    if mode not in {"direct", "group"}:
        raise CallError("Invalid call mode.", code="CALL_INVALID_STATE", http_status=409)
    # A direct call is application-limited to two durable members, but the
    # provider room is created with conference capacity so an ongoing direct
    # call can be promoted to a group without deleting/recreating the RTC room.
    return CALL_ROOM_MAX_PARTICIPANTS

def provision_call_room(room_name: str, *, max_participants: int) -> RoomAdminResult:
    """Provision a bounded Calls room through the shared LiveKit admin layer."""
    bounded = int(max_participants or 0)
    if bounded not in {DIRECT_CALL_MAX_PARTICIPANTS, GROUP_CALL_MAX_PARTICIPANTS}:
        raise CallError("Invalid call capacity.", code="CALL_INVALID_STATE", http_status=409)
    started = time.monotonic()
    result = ensure_room(room_name, max_participants=bounded)
    call_log(
        "room_provision",
        outcome="success" if result.ok else "failure",
        reason=result.category,
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    return result


def get_call_ws_url() -> str:
    try:
        return LiveKitService.get_ws_url()
    except Exception as exc:
        raise CallError(
            "Calling service is temporarily unavailable.",
            code="CALL_DEPENDENCY_UNAVAILABLE",
            http_status=503,
        ) from exc


def enqueue_room_provisioning(call_name: str) -> None:
    call_name = str(call_name or "").strip()
    if not call_name:
        return
    try:
        frappe.enqueue(
            "aos.tasks.calls.provision_call_room",
            queue="short",
            enqueue_after_commit=True,
            job_id=f"aos_call_provision:{call_name}",
            deduplicate=True,
            call_name=call_name,
        )
    except Exception as exc:
        call_log("room_provision_enqueue", outcome="failure", reason="dependency")
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
