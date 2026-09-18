"""Reviews API boundary helpers."""

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

from .errors import ReviewError, ReviewNotFoundError


def review_fail(exc: Exception, *, fallback: str = "Review request failed.") -> dict[str, Any]:
    if isinstance(exc, ReviewError):
        return fail(str(exc), error=exc.code, data=exc.data, http_status=exc.http_status)
    if isinstance(exc, MediaPermissionError):
        return fail("Review media access denied.", error="MEDIA_ACCESS_DENIED", http_status=403)
    if isinstance(exc, MediaStorageError):
        return fail("Review media is temporarily unavailable.", error="STORAGE_UNAVAILABLE", http_status=503)
    if isinstance(exc, (MediaValidationError, MediaConflictError, MediaNotFoundError, MediaError)):
        return fail("Invalid review media.", error="INVALID_REVIEW_MEDIA", http_status=422)
    return fail(fallback, error="INTERNAL_ERROR", http_status=500)


def run_review_api(
    operation: Callable[[], dict[str, Any]],
    *,
    fallback: str,
    log_title: str,
) -> dict[str, Any]:
    """Run one Reviews operation behind a bounded transaction savepoint."""

    savepoint = f"review_api_{uuid.uuid4().hex[:12]}"
    frappe.db.savepoint(savepoint)
    try:
        return operation()
    except (ReviewError, MediaError) as exc:
        frappe.db.rollback(save_point=savepoint)
        return review_fail(exc, fallback=fallback)
    except frappe.DoesNotExistError:
        frappe.db.rollback(save_point=savepoint)
        return review_fail(ReviewNotFoundError("Review not found."), fallback=fallback)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.log_error(frappe.get_traceback(), log_title)
        return fail(fallback, error="INTERNAL_ERROR", http_status=500)
