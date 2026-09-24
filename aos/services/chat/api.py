"""Chat API validation, savepoint and public-error boundary."""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Mapping

import frappe

from aos.api.shared.responses import fail

from .errors import ChatError
from .observability import chat_log
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
    value = str(code or "CHAT_INVALID_REQUEST").strip().upper()
    if value.startswith("CHAT_") or value in {
        "AUTH_REQUIRED", "LOGIN_REQUIRED", "RATE_LIMIT", "RATE_LIMITED", "ACCOUNT_DISABLED",
        "ACCOUNT_SUSPENDED", "ACCOUNT_DELETED", "ACCOUNT_DELETED_RESTORABLE",
        "MEDIA_NOT_FOUND", "MEDIA_ACCESS_DENIED", "MEDIA_OWNERSHIP_REQUIRED", "TRANSLATION_UNAVAILABLE",
    }:
        return value
    return {
        "VALIDATION_ERROR": "CHAT_INVALID_REQUEST",
        "INVALID_STATE": "CHAT_INVALID_STATE",
        "NOT_FOUND": "CHAT_NOT_FOUND",
        "RESOURCE_NOT_FOUND": "CHAT_NOT_FOUND",
        "PERMISSION_DENIED": "CHAT_ACCESS_DENIED",
        "FORBIDDEN": "CHAT_ACCESS_DENIED",
        "USER_BLOCKED": "CHAT_ACCESS_DENIED",
        "CONFLICT": "CHAT_CONFLICT",
        "INTERNAL_ERROR": "CHAT_INTERNAL_ERROR",
    }.get(value, value)


def _normalize_response(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok") is not False:
        return response
    normalized = dict(response)
    normalized["error"] = _normalize_error(response.get("error"))
    return normalized


def _reason(code: str) -> str:
    if code == "CHAT_UNKNOWN_FIELD":
        return "unknown_field"
    if code == "CHAT_INVALID_IDENTIFIER":
        return "identifier"
    if code in {"CHAT_ACCESS_DENIED", "AUTH_REQUIRED", "LOGIN_REQUIRED"}:
        return "access"
    if code in {"CHAT_CONFLICT", "CHAT_INVALID_STATE"}:
        return "state"
    if code in {"RATE_LIMIT", "RATE_LIMITED"}:
        return "rate_limit"
    if code == "CHAT_NOT_FOUND":
        return "not_found"
    if code == "CHAT_DEPENDENCY_UNAVAILABLE":
        return "dependency"
    return "validation"


def run_chat_api(
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
    except ChatError as exc:
        chat_log(
            operation_name,
            outcome="rejected",
            reason=_reason(exc.code),
            latency_ms=_latency_ms(),
        )
        return fail(exc.public_message, error=exc.code, data=exc.data, http_status=exc.http_status)

    savepoint = f"aos_chat_{uuid.uuid4().hex[:16]}" if transactional else None
    callbacks = _snapshot_callbacks() if transactional else {}
    outbox_flag = _outbox_flag() if transactional else None
    if savepoint:
        frappe.db.savepoint(savepoint)

    try:
        response = _normalize_response(implementation(**clean))
        if response.get("ok") is False and savepoint:
            _rollback(savepoint, callbacks, outbox_flag)
        code = str(response.get("error") or "")
        chat_log(
            operation_name,
            outcome="success" if response.get("ok") else ("conflict" if code in {"CHAT_CONFLICT", "CHAT_INVALID_STATE"} else "rejected"),
            reason="none" if response.get("ok") else _reason(code),
            latency_ms=_latency_ms(),
        )
        return response
    except ChatError as exc:
        if savepoint:
            _rollback(savepoint, callbacks, outbox_flag)
        chat_log(
            operation_name,
            outcome="rejected",
            reason=_reason(exc.code),
            latency_ms=_latency_ms(),
        )
        return fail(exc.public_message, error=exc.code, data=exc.data, http_status=exc.http_status)
    except Exception:
        if savepoint:
            _rollback(savepoint, callbacks, outbox_flag)
        chat_log(
            operation_name,
            outcome="failure",
            reason="none",
            latency_ms=_latency_ms(),
        )
        frappe.log_error("Chat API operation failed.", f"Chat {operation_name} failed")
        return fail("Chat request failed.", error="CHAT_INTERNAL_ERROR", http_status=500)
