"""Seller API boundary helpers."""

from __future__ import annotations

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


def run_seller_api(
    operation: Callable[[], dict[str, Any]],
    *,
    fallback: str,
    log_title: str,
) -> dict[str, Any]:
    try:
        return operation()
    except (SellerError, MediaError) as exc:
        try:
            frappe.db.rollback()
        except Exception:
            pass
        return seller_fail(exc, fallback=fallback)
    except frappe.DoesNotExistError:
        try:
            frappe.db.rollback()
        except Exception:
            pass
        return seller_fail(SellerNotFoundError("Seller not found."), fallback=fallback)
    except Exception:
        try:
            frappe.db.rollback()
        except Exception:
            pass
        frappe.log_error(frappe.get_traceback(), log_title)
        return fail(fallback, error="INTERNAL_ERROR", http_status=500)
