"""
Live Ads APIs (implementation).

Handles:
- attach_ad
- remove_ad
- pin_ad
- list_live_ads
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login, current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

from .constants import (
    ATTACH_AD_LIMIT_PER_MINUTE_PER_USER,
    REMOVE_AD_LIMIT_PER_MINUTE_PER_USER,
    PIN_AD_LIMIT_PER_MINUTE_PER_USER,
    LIST_LIVE_ADS_LIMIT_PER_MINUTE_PER_IP,
)

from .validators import (
    validate_live_exists,
    validate_user_is_seller,
    validate_ad_exists,
    validate_ad_active,
    validate_ad_belongs_to_seller,
    validate_ad_same_country,
    validate_ad_not_already_attached,
)

from .realtime import publish_pinned_ad


# HELPERS
def _get_identity(kwargs):
    user = current_user()
    session_id = kwargs.get("session_id")

    if not user:
        session_id = session_id or request_ip()

    return user, session_id


# ATTACH AD
def attach_ad_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:ad:attach:user:{user}",
        ttl_seconds=60,
        limit=ATTACH_AD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if rl:
        return rl

    live_id, err = require_id(kwargs.get("live_id"), "live_id")
    if err:
        return err

    ad_id, err = require_id(kwargs.get("ad_id"), "ad_id")
    if err:
        return err

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        err = validate_user_is_seller(live, user)
        if err:
            return err

        ad, err = validate_ad_exists(ad_id)
        if err:
            return err

        err = validate_ad_active(ad)
        if err:
            return err

        err = validate_ad_belongs_to_seller(ad, live)
        if err:
            return err

        err = validate_ad_same_country(ad, live)
        if err:
            return err

        err = validate_ad_not_already_attached(live_id, ad_id)
        if err:
            return err

        doc = frappe.new_doc("AOS Live Stream Ad")
        doc.live_stream = live_id
        doc.ad = ad_id
        doc.insert(ignore_permissions=True)

        return ok("Ad attached.")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Attach Ad Failed")
        frappe.db.rollback()
        return fail("Failed to attach ad.", code="INTERNAL_ERROR")


# REMOVE AD
def remove_ad_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:ad:remove:user:{user}",
        ttl_seconds=60,
        limit=REMOVE_AD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if rl:
        return rl

    live_ad_id, err = require_id(kwargs.get("live_ad_id"), "live_ad_id")
    if err:
        return err

    try:
        row = frappe.get_doc("AOS Live Stream Ad", live_ad_id)

        live, err = validate_live_exists(row.live_stream)
        if err:
            return err

        err = validate_user_is_seller(live, user)
        if err:
            return err

        frappe.delete_doc(
            "AOS Live Stream Ad",
            live_ad_id,
            ignore_permissions=True,
        )

        return ok("Ad removed.")

    except frappe.DoesNotExistError:
        return fail("Ad not found.", code="NOT_FOUND")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Remove Ad Failed")
        frappe.db.rollback()
        return fail("Failed to remove ad.", code="INTERNAL_ERROR")


# PIN AD
def pin_ad_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:ad:pin:user:{user}",
        ttl_seconds=60,
        limit=PIN_AD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if rl:
        return rl

    live_ad_id, err = require_id(kwargs.get("live_ad_id"), "live_ad_id")
    if err:
        return err

    try:
        row = frappe.get_doc("AOS Live Stream Ad", live_ad_id)

        live, err = validate_live_exists(row.live_stream)
        if err:
            return err

        err = validate_user_is_seller(live, user)
        if err:
            return err

        # Unpin all
        frappe.db.sql(
            """
            UPDATE `tabAOS Live Stream Ad`
            SET is_pinned = 0
            WHERE live_stream = %s
            """,
            (row.live_stream,),
        )

        # Pin selected
        frappe.db.set_value(
            "AOS Live Stream Ad",
            live_ad_id,
            {"is_pinned": 1},
            update_modified=False,
        )

        # Realtime
        publish_pinned_ad(row.live_stream, row.ad)

        return ok("Ad pinned.")

    except frappe.DoesNotExistError:
        return fail("Ad not found.", code="NOT_FOUND")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Pin Ad Failed")
        frappe.db.rollback()
        return fail("Failed to pin ad.", code="INTERNAL_ERROR")


# LIST LIVE ADS
def list_live_ads_impl(**kwargs):
    user, session_id = _get_identity(kwargs)

    rl = rate_limit(
        key=f"aos:live:ad:list:{user or session_id}",
        ttl_seconds=60,
        limit=LIST_LIVE_ADS_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests.",
    )
    if rl:
        return rl

    live_id = kwargs.get("live_id")

    if not live_id:
        return fail("live_id is required.", code="VALIDATION_ERROR")

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        rows = frappe.get_all(
            "AOS Live Stream Ad",
            filters={"live_stream": live_id},
            fields=[
                "name",
                "ad",
                "is_pinned",
                "sort_order",
                "creation",
            ],
            order_by="is_pinned desc, sort_order asc, creation asc",
        )

        return ok(
            "Live ads fetched.",
            data={"items": rows},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "List Live Ads Failed")
        return fail("Failed to fetch live ads.", code="INTERNAL_ERROR")
