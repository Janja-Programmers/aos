"""Reviews API boundary helpers."""

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

from .errors import ReviewError


def review_fail(exc: Exception, *, fallback: str = "Review request failed.") -> dict[str, Any]:
    if isinstance(exc, ReviewError):
        return fail(
            str(exc),
            error=exc.code,
            data=exc.data,
            http_status=exc.http_status,
        )
    if isinstance(exc, MediaPermissionError):
        return fail("Review media access denied.", error="MEDIA_ACCESS_DENIED", http_status=403)
    if isinstance(exc, MediaStorageError):
        return fail("Review media is temporarily unavailable.", error="STORAGE_UNAVAILABLE", http_status=503)
    if isinstance(exc, (MediaValidationError, MediaConflictError, MediaNotFoundError, MediaError)):
        # Do not reveal whether another user's media identifier exists. The
        # permission-specific branch above remains explicit for authenticated
        # ownership failures that the Media service intentionally classifies.
        return fail("Invalid review media.", error="INVALID_REVIEW_MEDIA", http_status=422)
    return fail(fallback, error="INTERNAL_ERROR", http_status=500)


def run_review_api(
    operation: Callable[[], dict[str, Any]],
    *,
    fallback: str,
    log_title: str,
) -> dict[str, Any]:
    try:
        return operation()
    except (ReviewError, MediaError) as exc:
        try:
            frappe.db.rollback()
        except Exception:
            pass
        return review_fail(exc, fallback=fallback)
    except frappe.DoesNotExistError:
        try:
            frappe.db.rollback()
        except Exception:
            pass
        from .errors import ReviewNotFoundError

        return review_fail(ReviewNotFoundError("Review not found."), fallback=fallback)
    except Exception:
        try:
            frappe.db.rollback()
        except Exception:
            pass
        frappe.log_error(frappe.get_traceback(), log_title)
        return fail(fallback, error="INTERNAL_ERROR", http_status=500)
