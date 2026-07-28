"""Seller API boundary helpers."""

from __future__ import annotations

import uuid
from typing import Any, Callable

import frappe

from aos.api.shared.responses import fail
from aos.services.media.media_service import (
    MediaConflictError,
    MediaError,
    MediaNotFoundError,
    MediaPermissionError,
    MediaStorageError,
    MediaValidationError,
)

from .errors import SellerError, SellerNotFoundError

_CALLBACK_MANAGER_NAMES = (
    "before_commit",
    "after_commit",
    "before_rollback",
    "after_rollback",
)


def seller_fail(exc: Exception, *, fallback: str = "Seller request failed.") -> dict[str, Any]:
    if isinstance(exc, SellerError):
        return fail(
            str(exc),
            error=exc.code,
            data=exc.data,
            http_status=exc.http_status,
        )
    if isinstance(exc, MediaPermissionError):
        return fail("Seller banner access denied.", error="MEDIA_ACCESS_DENIED", http_status=403)
    if isinstance(exc, MediaStorageError):
        return fail("Seller banner storage is temporarily unavailable.", error="STORAGE_UNAVAILABLE", http_status=503)
    if isinstance(exc, (MediaValidationError, MediaConflictError, MediaNotFoundError, MediaError)):
        return fail("Invalid seller banner.", error="INVALID_SELLER_BANNER", http_status=422)
    return fail(fallback, error="INTERNAL_ERROR", http_status=500)


def _snapshot_transaction_callbacks() -> dict[str, list[Any]]:
    """Capture Frappe transaction callbacks registered by this operation.

    Rolling SQL back to a savepoint does not remove in-memory after-commit or
    after-rollback callbacks. Restoring these lists prevents a rejected Seller
    mutation from running Media/outbox side effects after the API returns.
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
    """Rollback only this API operation, preserving the caller transaction.

    Frappe tests and internal callers may invoke more than one implementation
    function in a single outer transaction. A domain conflict must not erase a
    prior successful operation from that transaction. Normal HTTP requests
    retain the same atomic behaviour because Frappe still commits or rolls back
    the outer request transaction.
    """

    try:
        frappe.db.rollback(save_point=savepoint)
    except Exception:
        # Fail closed on runtimes without savepoint rollback support.
        frappe.db.rollback()
    finally:
        _restore_transaction_callbacks(callbacks_before)
        _restore_outbox_registration_flag(outbox_flag_before)


def run_seller_api(
    operation: Callable[[], dict[str, Any]],
    *,
    fallback: str,
    log_title: str,
    transactional: bool = True,
) -> dict[str, Any]:
    """Execute a Seller API operation behind a safe response boundary.

    Mutations use an operation-level savepoint so a handled Seller conflict
    rolls back only that operation rather than unrelated work in the caller's
    outer transaction. Read-only operations must set ``transactional=False``:
    request validation then runs before any database statement, preserving the
    fail-fast SQL-safety boundary for malformed filters and sort values.
    """

    savepoint = f"aos_seller_{uuid.uuid4().hex[:16]}" if transactional else None
    callbacks_before = _snapshot_transaction_callbacks() if transactional else {}
    outbox_flag_before = _outbox_registration_flag() if transactional else None
    if savepoint:
        frappe.db.savepoint(savepoint)

    def rollback_operation() -> None:
        if not savepoint:
            return
        _rollback_operation(
            savepoint=savepoint,
            callbacks_before=callbacks_before,
            outbox_flag_before=outbox_flag_before,
        )

    try:
        return operation()
    except (SellerError, MediaError) as exc:
        rollback_operation()
        return seller_fail(exc, fallback=fallback)
    except frappe.DoesNotExistError:
        rollback_operation()
        return seller_fail(SellerNotFoundError("Seller not found."), fallback=fallback)
    except Exception:
        rollback_operation()
        frappe.log_error(frappe.get_traceback(), log_title)
        return fail(fallback, error="INTERNAL_ERROR", http_status=500)
