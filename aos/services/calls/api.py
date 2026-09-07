"""Calls API validation, transaction and public-error boundary."""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Mapping

import frappe

from aos.api.shared.responses import fail

from .errors import CallError
from .observability import call_log
from .validation import EndpointSpec, validate_public_kwargs

_CALLBACK_MANAGER_NAMES = ("before_commit", "after_commit", "before_rollback", "after_rollback")


def _snapshot_callbacks() -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = {}
    for name in _CALLBACK_MANAGER_NAMES:
        functions = getattr(getattr(frappe.db, name, None), "_functions", None)
        if isinstance(functions, list):
            result[name] = list(functions)
    return result


def _restore_callbacks(snapshot: dict[str, list[Any]]) -> None:
    for name, functions in snapshot.items():
        current = getattr(getattr(frappe.db, name, None), "_functions", None)
        if isinstance(current, list):
            current[:] = functions


def _outbox_flag() -> bool | None:
    flags = getattr(getattr(frappe, "local", None), "flags", None)
    return None if flags is None else bool(getattr(flags, "aos_outbox_after_commit_registered", False))


def _restore_outbox_flag(value: bool | None) -> None:
    flags = getattr(getattr(frappe, "local", None), "flags", None)
    if flags is not None and value is not None:
        flags.aos_outbox_after_commit_registered = value


def _rollback(savepoint: str, callbacks: dict[str, list[Any]], outbox_flag: bool | None) -> None:
    try:
        frappe.db.rollback(save_point=savepoint)
    finally:
        _restore_callbacks(callbacks)
        _restore_outbox_flag(outbox_flag)


def _normalize_error(code: str | None) -> str:
    value = str(code or "CALL_INVALID_REQUEST").strip().upper()
    if value.startswith("CALL_") or value in {"AUTH_REQUIRED", "LOGIN_REQUIRED", "RATE_LIMIT", "RATE_LIMITED"}:
        return value
    return {
        "VALIDATION_ERROR": "CALL_INVALID_REQUEST",
        "INVALID_STATE": "CALL_INVALID_STATE",
        "NOT_FOUND": "CALL_NOT_FOUND",
        "RESOURCE_NOT_FOUND": "CALL_NOT_FOUND",
        "PERMISSION_DENIED": "CALL_ACCESS_DENIED",
        "FORBIDDEN": "CALL_ACCESS_DENIED",
        "USER_BLOCKED": "CALL_ACCESS_DENIED",
        "ACTIVE_CALL_EXISTS": "CALL_BUSY",
        "ACCOUNT_DISABLED": "CALL_ACCOUNT_UNAVAILABLE",
        "ACCOUNT_SUSPENDED": "CALL_ACCOUNT_UNAVAILABLE",
        "ACCOUNT_DELETED": "CALL_ACCOUNT_UNAVAILABLE",
        "ACCOUNT_DELETED_RESTORABLE": "CALL_ACCOUNT_UNAVAILABLE",
        "SERVICE_UNAVAILABLE": "CALL_DEPENDENCY_UNAVAILABLE",
        "PAYLOAD_TOO_LARGE": "CALL_INPUT_TOO_LARGE",
        "INTERNAL_ERROR": "CALL_INTERNAL_ERROR",
    }.get(value, "CALL_INVALID_REQUEST")


def _normalize_response(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok") is not False:
        return response
    normalized = dict(response)
    normalized["error"] = _normalize_error(response.get("error"))
    return normalized


def _reason(code: str) -> str:
    if code == "CALL_UNKNOWN_FIELD":
        return "unknown_field"
    if code == "CALL_ALIAS_CONFLICT":
        return "alias_conflict"
    if code in {"CALL_INVALID_IDENTIFIER", "CALL_INVALID_CURSOR"}:
        return "identifier"
    if code in {"CALL_ACCESS_DENIED", "CALL_ACCOUNT_UNAVAILABLE", "AUTH_REQUIRED", "LOGIN_REQUIRED"}:
        return "access"
    if code == "CALL_BUSY":
        return "busy"
    if code == "CALL_INVALID_STATE":
        return "state"
    if code in {"RATE_LIMIT", "RATE_LIMITED"}:
        return "rate_limit"
    if code == "CALL_DEPENDENCY_UNAVAILABLE":
        return "dependency"
    return "validation"


def run_call_api(
    implementation: Callable[..., dict[str, Any]],
    kwargs: Mapping[str, Any],
    *,
    spec: EndpointSpec,
    operation_name: str,
    transactional: bool,
) -> dict[str, Any]:
    started = time.monotonic()

    def _latency_ms() -> int:
        return max(0, int((time.monotonic() - started) * 1000))

    try:
        clean = validate_public_kwargs(kwargs, spec)
    except CallError as exc:
        call_log(
            operation_name,
            outcome="rejected",
            reason=_reason(exc.code),
            latency_ms=_latency_ms(),
        )
        return fail(exc.public_message, error=exc.code, data=exc.data, http_status=exc.http_status)

    savepoint = f"aos_call_{uuid.uuid4().hex[:16]}" if transactional else None
    callbacks = _snapshot_callbacks() if transactional else {}
    outbox_flag = _outbox_flag() if transactional else None
    if savepoint:
        frappe.db.savepoint(savepoint)

    try:
        response = _normalize_response(implementation(**clean))
        if response.get("ok") is False and savepoint:
            _rollback(savepoint, callbacks, outbox_flag)
        code = str(response.get("error") or "")
        call_log(
            operation_name,
            outcome="success" if response.get("ok") else ("conflict" if code in {"CALL_BUSY", "CALL_INVALID_STATE"} else "rejected"),
            reason="none" if response.get("ok") else _reason(code),
            latency_ms=_latency_ms(),
        )
        return response
    except CallError as exc:
        if savepoint:
            _rollback(savepoint, callbacks, outbox_flag)
        call_log(
            operation_name,
            outcome="rejected",
            reason=_reason(exc.code),
            latency_ms=_latency_ms(),
        )
        return fail(exc.public_message, error=exc.code, data=exc.data, http_status=exc.http_status)
    except Exception:
        if savepoint:
            _rollback(savepoint, callbacks, outbox_flag)
        call_log(
            operation_name,
            outcome="failure",
            reason="internal",
            latency_ms=_latency_ms(),
        )
        frappe.log_error("Calls API operation failed.", f"Calls {operation_name} failed")
        return fail("Call request failed.", error="CALL_INTERNAL_ERROR", http_status=500)
