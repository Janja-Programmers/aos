"""Social API boundary and public-safe error mapping."""

from __future__ import annotations

from typing import Any, Callable

import frappe

from aos.api.shared.responses import fail

from .errors import SocialError
from .observability import social_log


def _reason_for_code(code: str) -> str:
    if code == "SOCIAL_INVALID_CURSOR":
        return "cursor"
    if code == "SOCIAL_UNKNOWN_FIELD":
        return "unknown_field"
    if code == "SOCIAL_ALIAS_CONFLICT":
        return "alias_conflict"
    if code == "SOCIAL_SELF_ACTION":
        return "self_action"
    if code == "SOCIAL_BLOCKED":
        return "blocked"
    if code in {"SOCIAL_ACTOR_UNAVAILABLE", "SOCIAL_PROFILE_UNAVAILABLE"}:
        return "unavailable"
    if code == "SOCIAL_CONFLICT":
        return "duplicate"
    return "validation"


def social_fail(exc: Exception, *, fallback: str, operation_name: str) -> dict[str, Any]:
    if isinstance(exc, SocialError):
        social_log(
            operation_name,
            outcome="conflict" if exc.http_status == 409 else "rejected",
            reason=_reason_for_code(exc.code),
        )
        return fail(str(exc), error=exc.code, data=exc.data, http_status=exc.http_status)
    social_log(operation_name, outcome="failure", reason="internal")
    return fail(fallback, error="SOCIAL_INTERNAL_ERROR", http_status=500)


def run_social_api(
    operation: Callable[[], dict[str, Any]],
    *,
    fallback: str,
    log_title: str,
    operation_name: str = "relationship",
) -> dict[str, Any]:
    try:
        return operation()
    except SocialError as exc:
        try:
            frappe.db.rollback()
        except Exception:
            pass
        return social_fail(exc, fallback=fallback, operation_name=operation_name)
    except Exception:
        try:
            frappe.db.rollback()
        except Exception:
            pass
        social_log(operation_name, outcome="failure", reason="internal")
        frappe.log_error("Social API operation failed.", log_title)
        return fail(fallback, error="SOCIAL_INTERNAL_ERROR", http_status=500)
