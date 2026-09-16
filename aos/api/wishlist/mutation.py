"""Shared transport boundary for explicit Wishlist add/remove operations."""

from __future__ import annotations

from collections.abc import Callable

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.services.ads.api import run_ads_api
from aos.services.wishlist.constants import WISHLIST_MUTATION_LIMIT_PER_MINUTE_PER_USER
from aos.services.wishlist.service import WishlistMutationResult, WishlistService
from aos.services.wishlist.validation import normalize_wishlist_mutation


def _schedule_activity(*, user: str, result: WishlistMutationResult) -> None:
    """Queue non-authoritative Activity synchronization after commit.

    Activity is deliberately best-effort. Wishlist relationship/count writes are
    authoritative and must never be rolled back because Redis/workers or the
    Activity feature are temporarily unavailable.
    """
    if not result.changed:
        return

    try:
        frappe.enqueue(
            "aos.tasks.wishlist.sync_wishlist_activity",
            queue="short",
            enqueue_after_commit=True,
            user=user,
            ad_id=result.ad_name,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Wishlist Activity Scheduling Failed")


def run_wishlist_mutation(
    *,
    kwargs: dict[str, object],
    action: str,
    mutate: Callable[[WishlistService, str, str], WishlistMutationResult],
) -> dict[str, object]:
    user, auth_error = require_login()
    if auth_error:
        return auth_error

    limited = rate_limit(
        key=rate_limit_key("wishlist", "mutation", user, request_ip()),
        ttl_seconds=60,
        limit=WISHLIST_MUTATION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited

    def _operation():
        public_ad_id = normalize_wishlist_mutation(kwargs)
        result = mutate(WishlistService(), user, public_ad_id)
        _schedule_activity(user=user, result=result)
        return ok(
            "Added to wishlist." if result.wishlisted else "Removed from wishlist.",
            data={
                "ad_id": result.public_ad_id,
                "wishlisted": result.wishlisted,
                "changed": result.changed,
                "wishlist_count": result.wishlist_count,
            },
        )

    return run_ads_api(
        _operation,
        fallback=f"Failed to {action} wishlist item.",
        log_title=f"AOS Wishlist {action.title()} Failed",
        transactional=True,
    )
