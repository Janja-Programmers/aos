"""Social API boundary and public-safe error mapping.

Each implementation call owns only its operation-level savepoint. Handled
validation/conflict failures must not roll back unrelated writes already made by
an outer Frappe transaction (for example, another implementation call in a test
or a composed internal workflow). Frappe still owns the final request commit or
rollback.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

import frappe

from aos.api.shared.responses import fail

from .errors import SocialError
from .observability import social_log

_CALLBACK_MANAGER_NAMES = (
    "before_commit",
    "after_commit",
    "before_rollback",
    "after_rollback",
)


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


def _snapshot_transaction_callbacks() -> dict[str, list[Any]]:
    """Capture callbacks registered before the Social operation starts.

    SQL savepoint rollback does not remove callbacks stored in Frappe's in-memory
    callback managers. Restoring the lists prevents rolled-back notification or
    outbox work from executing after the outer transaction commits.
    """

    snapshot: dict[str, list[Any]] = {}
    for name in _CALLBACK_MANAGER_NAMES:
        manager = getattr(frappe.db, name, None)
        functions = getattr(manager, "_functions", None)
        if isinstance(functions, list):
            snapshot[name] = list(functions)
    return snapshot


def _restore_transaction_callbacks(snapshot: dict[str, list[Any]]) -> None:
    for name, functions in snapshot.items():
        manager = getattr(frappe.db, name, None)
        current = getattr(manager, "_functions", None)
        if isinstance(current, list):
            current[:] = functions


def _outbox_registration_flag() -> bool | None:
    flags = getattr(getattr(frappe, "local", None), "flags", None)
    if flags is None:
        return None
    return bool(getattr(flags, "aos_outbox_after_commit_registered", False))


def _restore_outbox_registration_flag(value: bool | None) -> None:
    flags = getattr(getattr(frappe, "local", None), "flags", None)
    if flags is not None and value is not None:
        flags.aos_outbox_after_commit_registered = value


def _rollback_operation(
    *,
    savepoint: str,
    callbacks_before: dict[str, list[Any]],
    outbox_flag_before: bool | None,
) -> None:
    """Roll back only this Social operation, preserving its caller transaction."""

    try:
        frappe.db.rollback(save_point=savepoint)
    except Exception:
        # Fail closed on runtimes without savepoint rollback support. Supported
        # Frappe 17 runtimes use the savepoint branch above.
        frappe.db.rollback()
    finally:
        _restore_transaction_callbacks(callbacks_before)
        _restore_outbox_registration_flag(outbox_flag_before)


def run_social_api(
    operation: Callable[[], dict[str, Any]],
    *,
    fallback: str,
    log_title: str,
    operation_name: str = "relationship",
) -> dict[str, Any]:
    """Execute one Social operation without owning the outer transaction.

    A unique savepoint protects mutations and also makes handled read-validation
    failures harmless to prior work in the same transaction. This is important
    for Frappe tests and internal composition while preserving normal HTTP
    request atomicity.
    """

    savepoint = f"aos_social_{uuid.uuid4().hex[:16]}"
    callbacks_before = _snapshot_transaction_callbacks()
    outbox_flag_before = _outbox_registration_flag()
    frappe.db.savepoint(savepoint)

    def rollback_operation() -> None:
        _rollback_operation(
            savepoint=savepoint,
            callbacks_before=callbacks_before,
            outbox_flag_before=outbox_flag_before,
        )

    try:
        return operation()
    except SocialError as exc:
        rollback_operation()
        return social_fail(exc, fallback=fallback, operation_name=operation_name)
    except Exception:
        rollback_operation()
        social_log(operation_name, outcome="failure", reason="internal")
        frappe.log_error("Social API operation failed.", log_title)
        return fail(fallback, error="SOCIAL_INTERNAL_ERROR", http_status=500)
