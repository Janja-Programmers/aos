"""Race-safe, idempotent wishlist mutation."""

from __future__ import annotations

import frappe
from frappe.utils import getdate, nowdate

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.services.ads.constants import WISHLIST_TOGGLE_FIELDS
from aos.services.ads.validation import ensure_known_fields, normalize_flag, normalize_identifier

from aos.api.ads.activity import hide_ad_wishlist_activity, record_ad_wishlist_activity
from .constants import WISHLIST_LIMIT_PER_MINUTE_PER_IP


def toggle_wishlist_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    limited = rate_limit(
        key=f"aos:wishlist:toggle:user:{user}:ip:{request_ip()}",
        ttl_seconds=60,
        limit=WISHLIST_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited

    try:
        ensure_known_fields(kwargs, WISHLIST_TOGGLE_FIELDS)
        ad_id = normalize_identifier(kwargs.get("ad_id") or kwargs.get("id"), field="ad_id", required=True)
        requested = None
        if "wishlisted" in kwargs:
            requested = bool(normalize_flag(kwargs.get("wishlisted"), field="wishlisted"))

        ad = frappe.db.get_value(
            "AOS Ad",
            ad_id,
            ["name", "status", "seller", "expires_on"],
            as_dict=True,
        )
        if not ad or ad.status != "Active" or (ad.expires_on and getdate(ad.expires_on) < getdate(nowdate())):
            return fail("Ad not found.", error="AD_NOT_FOUND")
        seller = frappe.db.get_value("AOS Seller", ad.seller, ["user", "status"], as_dict=True)
        if not seller or seller.status != "Active":
            return fail("Ad not found.", error="AD_NOT_FOUND")
        blocked = frappe.db.sql(
            """
            SELECT 1 FROM `tabAOS User Block`
            WHERE status = 'Active'
              AND ((blocker_user = %s AND blocked_user = %s)
                OR (blocker_user = %s AND blocked_user = %s))
            LIMIT 1
            """,
            (user, seller.user, seller.user, user),
        )
        if blocked:
            return fail("Ad not found.", error="AD_NOT_FOUND")

        # Serialize requests for the same logical pair. The unique database
        # constraint added by the Ads hardening patch remains the final guard.
        rows = frappe.db.sql(
            """
            SELECT name, status FROM `tabAOS Wishlist`
            WHERE user = %s AND ad = %s
            ORDER BY creation ASC, name ASC
            LIMIT 1 FOR UPDATE
            """,
            (user, ad_id),
            as_dict=True,
        )
        doc = frappe.get_doc("AOS Wishlist", rows[0].name) if rows else None
        current = bool(doc and doc.status == "Active")
        desired = (not current) if requested is None else requested

        if doc:
            if current != desired:
                doc.status = "Active" if desired else "Removed"
                doc.save(ignore_permissions=True)
        elif desired:
            doc = frappe.get_doc(
                {"doctype": "AOS Wishlist", "user": user, "ad": ad_id, "status": "Active"}
            )
            try:
                doc.insert(ignore_permissions=True)
            except frappe.DuplicateEntryError:
                existing = frappe.db.get_value(
                    "AOS Wishlist", {"user": user, "ad": ad_id}, "name"
                )
                if not existing:
                    raise
                doc = frappe.get_doc("AOS Wishlist", existing)
                if doc.status != "Active":
                    doc.status = "Active"
                    doc.save(ignore_permissions=True)

        if desired:
            record_ad_wishlist_activity(user=user, ad_id=ad_id)
        else:
            hide_ad_wishlist_activity(user=user, ad_id=ad_id)
        return ok("Wishlist updated.", data={"wishlisted": desired, "changed": current != desired})
    except (ValueError, TypeError) as exc:
        code = str(getattr(exc, "code", "VALIDATION_ERROR"))
        return fail("Invalid wishlist request.", error=code)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Toggle Wishlist Failed")
        frappe.db.rollback()
        return fail("Failed to update wishlist.", error="INTERNAL_ERROR")
