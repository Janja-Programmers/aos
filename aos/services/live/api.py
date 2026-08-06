"""Live API validation, transaction and public-error boundary."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Mapping

import frappe

from aos.api.shared.responses import fail

from .errors import LiveError
from .observability import live_log
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
    value = str(code or "LIVE_INVALID_REQUEST").strip().upper()
    if value.startswith("LIVE_") or value in {
        "AUTH_REQUIRED", "LOGIN_REQUIRED", "RATE_LIMIT", "RATE_LIMITED",
        "MEDIA_NOT_FOUND", "MEDIA_ACCESS_DENIED", "MEDIA_OWNERSHIP_REQUIRED",
        "STORAGE_UNAVAILABLE", "ACCOUNT_DISABLED", "ACCOUNT_SUSPENDED",
    }:
        return value
    return {
        "VALIDATION_ERROR": "LIVE_INVALID_REQUEST",
        "INVALID_STATE": "LIVE_INVALID_STATE",
        "NOT_FOUND": "LIVE_NOT_FOUND",
        "RESOURCE_NOT_FOUND": "LIVE_NOT_FOUND",
        "PERMISSION_DENIED": "LIVE_ACCESS_DENIED",
        "FORBIDDEN": "LIVE_ACCESS_DENIED",
        "USER_BLOCKED": "LIVE_ACCESS_DENIED",
        "CONFLICT": "LIVE_CONFLICT",
        "COHOST_SLOT_UNAVAILABLE": "LIVE_COHOST_SLOT_UNAVAILABLE",
        "SERVICE_UNAVAILABLE": "LIVE_DEPENDENCY_UNAVAILABLE",
        "INTERNAL_ERROR": "LIVE_INTERNAL_ERROR",
    }.get(value, value)


def _normalize_response(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok") is not False:
        return response
    normalized = dict(response)
    normalized["error"] = _normalize_error(response.get("error"))
    return normalized


def _reason(code: str) -> str:
    if code == "LIVE_UNKNOWN_FIELD":
        return "unknown_field"
    if code == "LIVE_ALIAS_CONFLICT":
        return "alias_conflict"
    if code == "LIVE_INVALID_IDENTIFIER":
        return "identifier"
    if code in {"LIVE_ACCESS_DENIED", "AUTH_REQUIRED", "LOGIN_REQUIRED"}:
        return "access"
    if code in {"LIVE_INVALID_STATE", "LIVE_COHOST_SLOT_UNAVAILABLE", "LIVE_CONFLICT"}:
        return "state"
    if code in {"RATE_LIMIT", "RATE_LIMITED"}:
        return "rate_limit"
    if code in {"LIVE_DEPENDENCY_UNAVAILABLE"}:
        return "dependency"
    return "validation"


def run_live_api(
    implementation: Callable[..., dict[str, Any]],
    kwargs: Mapping[str, Any],
    *,
    spec: EndpointSpec,
    operation_name: str,
    transactional: bool,
) -> dict[str, Any]:
    try:
        clean = validate_public_kwargs(kwargs, spec)
    except LiveError as exc:
        live_log(operation_name, outcome="rejected", reason=_reason(exc.code))
        return fail(str(exc), error=exc.code, data=exc.data, http_status=exc.http_status)

    savepoint = f"aos_live_{uuid.uuid4().hex[:16]}" if transactional else None
    callbacks = _snapshot_callbacks() if transactional else {}
    outbox_flag = _outbox_flag() if transactional else None
    if savepoint:
        frappe.db.savepoint(savepoint)

    try:
        response = _normalize_response(implementation(**clean))
        if response.get("ok") is False and savepoint:
            _rollback(savepoint, callbacks, outbox_flag)
        code = str(response.get("error") or "")
        live_log(
            operation_name,
            outcome="success" if response.get("ok") else ("conflict" if code in {"LIVE_CONFLICT", "LIVE_INVALID_STATE"} else "rejected"),
            reason="none" if response.get("ok") else _reason(code),
        )
        return response
    except LiveError as exc:
        if savepoint:
            _rollback(savepoint, callbacks, outbox_flag)
        live_log(operation_name, outcome="rejected", reason=_reason(exc.code))
        return fail(str(exc), error=exc.code, data=exc.data, http_status=exc.http_status)
    except Exception:
        if savepoint:
            _rollback(savepoint, callbacks, outbox_flag)
        live_log(operation_name, outcome="failure", reason="internal")
        frappe.log_error("Live API operation failed.", f"Live {operation_name} failed")
        return fail("Live request failed.", error="LIVE_INTERNAL_ERROR", http_status=500)
