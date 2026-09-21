"""Shorts API transaction and public-error boundary."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Mapping

import frappe

from aos.api.shared.responses import fail

from .errors import ShortsError
from .observability import shorts_log
from .validation import EndpointSpec, validate_public_kwargs

_CALLBACK_MANAGER_NAMES = ("before_commit", "after_commit", "before_rollback", "after_rollback")


def _snapshot_callbacks() -> dict[str, list[Any]]:
    snapshot: dict[str, list[Any]] = {}
    for name in _CALLBACK_MANAGER_NAMES:
        manager = getattr(frappe.db, name, None)
        functions = getattr(manager, "_functions", None)
        if isinstance(functions, list):
            snapshot[name] = list(functions)
    return snapshot


def _restore_callbacks(snapshot: dict[str, list[Any]]) -> None:
    for name, functions in snapshot.items():
        manager = getattr(frappe.db, name, None)
        current = getattr(manager, "_functions", None)
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


def _normalized_code(response: Mapping[str, Any]) -> str:
    code = str(response.get("error") or "SHORTS_INVALID_REQUEST").strip().upper()
    if code.startswith("SHORTS_") or code in {
        "AUTH_REQUIRED", "LOGIN_REQUIRED", "RATE_LIMIT", "RATE_LIMITED",
        "SELLER_REQUIRED", "SELLER_INACTIVE", "MEDIA_NOT_FOUND",
        "MEDIA_ACCESS_DENIED", "STORAGE_UNAVAILABLE", "COMMENTS_DISABLED",
    }:
        return code
    aliases = {
        "VALIDATION_ERROR": "SHORTS_INVALID_REQUEST",
        "INVALID_STATE": "SHORTS_INVALID_STATE",
        "FORBIDDEN": "SHORTS_ACCESS_DENIED",
        "NOT_FOUND": "SHORTS_NOT_FOUND",
        "INTERNAL_ERROR": "SHORTS_INTERNAL_ERROR",
        "PROCESSING_FAILED": "SHORTS_PROCESSING_FAILED",
        "FILE_MISSING": "SHORTS_MEDIA_MISSING",
    }
    return aliases.get(code, code)


def _normalize_failure(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok") is not False:
        return response
    normalized = dict(response)
    normalized["error"] = _normalized_code(response)
    return normalized


def run_shorts_api(
    implementation: Callable[..., dict[str, Any]],
    kwargs: Mapping[str, Any],
    *,
    spec: EndpointSpec,
    operation_name: str,
    transactional: bool,
) -> dict[str, Any]:
    try:
        clean = validate_public_kwargs(kwargs, spec)
    except ShortsError as exc:
        shorts_log(operation_name, outcome="rejected", reason=exc.code.lower())
        return fail(str(exc), error=exc.code, data=exc.data, http_status=exc.http_status)

    savepoint = f"aos_shorts_{uuid.uuid4().hex[:16]}" if transactional else None
    callbacks = _snapshot_callbacks() if transactional else {}
    outbox_flag = _outbox_flag() if transactional else None
    if savepoint:
        frappe.db.savepoint(savepoint)

    try:
        response = _normalize_failure(implementation(**clean))
        if response.get("ok") is False and savepoint:
            _rollback(savepoint, callbacks, outbox_flag)
        shorts_log(
            operation_name,
            outcome="success" if response.get("ok") else "rejected",
            reason=str(response.get("error") or "none").lower(),
        )
        return response
    except ShortsError as exc:
        if savepoint:
            _rollback(savepoint, callbacks, outbox_flag)
        shorts_log(operation_name, outcome="rejected", reason=exc.code.lower())
        return fail(str(exc), error=exc.code, data=exc.data, http_status=exc.http_status)
    except Exception:
        if savepoint:
            _rollback(savepoint, callbacks, outbox_flag)
        shorts_log(operation_name, outcome="failure", reason="internal")
        frappe.log_error(frappe.get_traceback(), f"Shorts {operation_name} failed")
        return fail("Shorts request failed.", error="SHORTS_INTERNAL_ERROR", http_status=500)
