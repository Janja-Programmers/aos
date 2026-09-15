"""Ads API boundary helpers."""

from __future__ import annotations

from typing import Any, Callable

import frappe

from aos.api.shared.responses import fail
from aos.services.catalog.errors import CatalogError, public_catalog_message
from aos.services.media.media_service import (
    MediaError, MediaNotFoundError, MediaPermissionError, MediaStorageError, MediaValidationError,
)

from .errors import AdsError, public_ads_message


def ads_fail(exc: Exception, *, fallback: str = "Ads request failed.") -> dict[str, Any]:
    if isinstance(exc, AdsError):
        return fail(
            public_ads_message(exc, fallback=fallback),
            error=exc.code,
            http_status=exc.http_status,
        )
    if isinstance(exc, CatalogError):
        return fail(
            public_catalog_message(exc),
            error=exc.code,
            http_status=exc.http_status,
        )
    if isinstance(exc, MediaNotFoundError):
        return fail("Ad media not found.", error=exc.code, http_status=404)
    if isinstance(exc, MediaPermissionError):
        return fail("Ad media is not available.", error=exc.code, http_status=403)
    if isinstance(exc, MediaStorageError):
        return fail("Media storage is temporarily unavailable.", error=exc.code, http_status=503)
    if isinstance(exc, MediaValidationError):
        return fail("Invalid ad media.", error=exc.code, http_status=422)
    if isinstance(exc, MediaError):
        return fail("Ad media request failed.", error=exc.code, http_status=400)
    return fail(fallback, error="INTERNAL_ERROR", http_status=500)


def run_ads_api(
    operation: Callable[[], dict[str, Any]],
    *,
    fallback: str,
    log_title: str,
) -> dict[str, Any]:
    """Run one Ads operation inside a request-local savepoint.

    The API boundary converts domain exceptions into response envelopes, so it
    must explicitly roll back partial writes before returning a failure. A
    savepoint protects only this operation and never rolls back unrelated outer
    transaction work. Nested Ads operations use independent savepoints.
    """
    savepoint = f"aos_ads_{frappe.generate_hash(length=10)}"
    frappe.db.savepoint(savepoint)
    try:
        response = operation()
        if isinstance(response, dict) and not response.get("ok"):
            frappe.db.rollback(save_point=savepoint)
        return response
    except (AdsError, CatalogError, MediaError) as exc:
        frappe.db.rollback(save_point=savepoint)
        return ads_fail(exc, fallback=fallback)
    except frappe.DoesNotExistError:
        frappe.db.rollback(save_point=savepoint)
        from .errors import AdsNotFoundError

        return ads_fail(AdsNotFoundError("Ad not found."), fallback=fallback)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.log_error(frappe.get_traceback(), log_title)
        return fail(fallback, error="INTERNAL_ERROR", http_status=500)
