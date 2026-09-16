"""Canonical public Ad visibility checks shared by client-facing consumers."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import getdate, nowdate

from .errors import AdsNotFoundError


def require_public_ad_for_viewer(*, public_id: Any, viewer: str = "Guest") -> object:
    """Return the canonical public Ad row or fail without leaking why it is hidden.

    This is the authoritative single-Ad eligibility boundary for consumers such
    as Ad detail and Wishlist. Derived/indexed systems may propose candidates,
    but this database check decides whether the Ad is publicly viewable now.
    """

    clean_public_id = str(public_id or "").strip()
    if not clean_public_id:
        raise AdsNotFoundError("Ad not found.")

    clean_viewer = str(viewer or "Guest").strip() or "Guest"
    values: dict[str, Any] = {
        "public_id": clean_public_id,
        "today": getdate(nowdate()),
    }
    conditions = [
        "a.public_id = %(public_id)s",
        "a.status = 'Active'",
        "s.status = 'Active'",
        "u.enabled = 1",
        "COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'",
        "(a.expires_on IS NULL OR a.expires_on >= %(today)s)",
    ]
    if clean_viewer != "Guest":
        values["viewer"] = clean_viewer
        conditions.append(
            """NOT EXISTS (
                SELECT 1
                FROM `tabAOS User Block` b
                WHERE b.status = 'Active'
                  AND ((b.blocker_user = %(viewer)s AND b.blocked_user = s.user)
                    OR (b.blocker_user = s.user AND b.blocked_user = %(viewer)s))
            )"""
        )

    rows = frappe.db.sql(
        f"""
        SELECT
            a.name,
            a.public_id,
            a.seller,
            s.user AS seller_user,
            loc.location AS location_name
        FROM `tabAOS Ad` a
        INNER JOIN `tabAOS Seller` s ON s.name = a.seller
        LEFT JOIN `tabAOS Location` loc ON loc.name = a.location
        INNER JOIN `tabAOS Profile` p ON p.user = s.user
        INNER JOIN `tabUser` u ON u.name = s.user
        WHERE {' AND '.join(conditions)}
        LIMIT 1
        """,
        values,
        as_dict=True,
    )
    if not rows:
        raise AdsNotFoundError("Ad not found.")
    return rows[0]
