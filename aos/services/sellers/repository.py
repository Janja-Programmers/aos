"""Seller persistence and query boundaries.

Seller owns Seller rows, ownership checks, Seller location records and Seller
viewport rows. Maps consumes these records for routing/geospatial projection;
Maps does not mutate Seller business records.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.blocking import is_blocked_between

from .constants import SELLER_DOCTYPE, STATUS_ACTIVE, VERIFICATION_DOCTYPE
from .identity import resolve_public_seller_id

SELLER_LOCATION_FIELDS = [
    "name",
    "public_id",
    "user",
    "status",
    "has_location",
    "location_version",
    "location_name",
    "location_instructions",
    "latitude",
    "longitude",
    "display_address",
    "locality",
    "region",
    "country_code",
    "location_updated_at",
]


def get_by_user(user: str, *, fields: list[str] | None = None):
    clean_user = str(user or "").strip()
    if not clean_user or clean_user == "Guest":
        return None
    selected = fields or ["name", "public_id", "user", "status"]
    return frappe.db.get_value(SELLER_DOCTYPE, {"user": clean_user}, selected, as_dict=True)


def lock_by_user(user: str):
    clean_user = str(user or "").strip()
    rows = frappe.db.sql(
        "SELECT name FROM `tabAOS Seller` WHERE user = %s FOR UPDATE",
        (clean_user,),
        as_dict=True,
    )
    return frappe.get_doc(SELLER_DOCTYPE, rows[0].name) if rows else None


def lock_by_name(name: str):
    clean_name = str(name or "").strip()
    rows = frappe.db.sql(
        "SELECT name FROM `tabAOS Seller` WHERE name = %s FOR UPDATE",
        (clean_name,),
        as_dict=True,
    )
    return frappe.get_doc(SELLER_DOCTYPE, rows[0].name) if rows else None


def get_location_for_user(user: str) -> dict[str, Any] | None:
    return get_by_user(user, fields=SELLER_LOCATION_FIELDS)


def get_public_location(public_id: str, *, viewer: str | None) -> dict[str, Any] | None:
    seller_name = resolve_public_seller_id(public_id)
    if not seller_name:
        return None
    rows = frappe.db.sql(
        """
        SELECT
            s.name, s.public_id, s.user, s.status, s.has_location,
            COALESCE(s.location_version, 0) AS location_version,
            s.location_name, s.location_instructions, s.latitude, s.longitude,
            s.display_address, s.locality, s.region, s.country_code,
            s.location_updated_at
        FROM `tabAOS Seller` s
        INNER JOIN `tabUser` u ON u.name = s.user
        INNER JOIN `tabAOS Profile` p ON p.user = s.user
        WHERE s.name = %s
          AND s.status = %s
          AND u.enabled = 1
          AND COALESCE(p.account_status, 'Active') = 'Active'
        LIMIT 1
        """,
        (seller_name, STATUS_ACTIVE),
        as_dict=True,
    )
    if not rows:
        return None
    seller = rows[0]
    clean_viewer = str(viewer or "").strip()
    if (
        clean_viewer
        and clean_viewer != "Guest"
        and clean_viewer != seller.user
        and is_blocked_between(clean_viewer, seller.user)
    ):
        return None
    return seller


def get_route_destination(public_id: str, *, viewer: str | None) -> dict[str, Any] | None:
    seller = get_public_location(public_id, viewer=viewer)
    if not seller or not bool(seller.get("has_location")):
        return None
    try:
        latitude = float(seller.get("latitude"))
        longitude = float(seller.get("longitude"))
    except (TypeError, ValueError, OverflowError):
        return None
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return None
    return {
        "seller_id": seller.get("public_id"),
        "latitude": latitude,
        "longitude": longitude,
    }


def get_verification_for_user(user: str) -> dict[str, Any] | None:
    clean_user = str(user or "").strip()
    if not clean_user:
        return None
    return frappe.db.get_value(
        VERIFICATION_DOCTYPE,
        {"user": clean_user},
        ["name", "verification_type", "status", "business_category", "verified_on", "modified"],
        as_dict=True,
    )


def lock_verification_for_user(user: str) -> dict[str, Any] | None:
    """Return the canonical Verification row while holding its transaction lock.

    Seller creation uses this to serialize against a concurrent Verification
    decision. Without the lock, an approval could observe no Seller and finish
    its projection while Seller creation was still using a stale pre-approval
    snapshot.
    """

    clean_user = str(user or "").strip()
    if not clean_user:
        return None
    rows = frappe.db.sql(
        f"""
        SELECT name, verification_type, status, business_category, verified_on, modified
        FROM `tab{VERIFICATION_DOCTYPE}`
        WHERE user = %s
        LIMIT 1
        FOR UPDATE
        """,
        (clean_user,),
        as_dict=True,
    )
    return rows[0] if rows else None


def list_map_rows(
    request: dict[str, Any],
    *,
    viewer: str | None,
    maximum_rows: int,
) -> list[dict[str, Any]]:
    conditions = [
        "s.status = %s",
        "u.enabled = 1",
        "COALESCE(p.account_status, 'Active') = 'Active'",
        "COALESCE(s.has_location, 0) = 1",
        "s.latitude IS NOT NULL",
        "s.longitude IS NOT NULL",
        "s.latitude BETWEEN %s AND %s",
    ]
    params: list[Any] = [STATUS_ACTIVE, request["south"], request["north"]]
    if request.get("crosses_antimeridian"):
        conditions.append("(s.longitude >= %s OR s.longitude <= %s)")
    else:
        conditions.append("s.longitude BETWEEN %s AND %s")
    params.extend([request["west"], request["east"]])

    clean_viewer = str(viewer or "").strip()
    if clean_viewer and clean_viewer != "Guest":
        conditions.append("s.user != %s")
        params.append(clean_viewer)
        conditions.append(
            """
            NOT EXISTS (
                SELECT 1 FROM `tabAOS User Block` b
                WHERE b.status = 'Active'
                  AND ((b.blocker_user = %s AND b.blocked_user = s.user)
                    OR (b.blocked_user = %s AND b.blocker_user = s.user))
            )
            """
        )
        params.extend([clean_viewer, clean_viewer])
    if request.get("seller_type"):
        conditions.append("s.seller_type = %s")
        params.append(request["seller_type"])
    if request.get("business_category"):
        conditions.append("s.business_category = %s")
        params.append(request["business_category"])
    if request.get("is_verified") is not None:
        conditions.append("CASE WHEN v.status = 'Approved' THEN 1 ELSE 0 END = %s")
        params.append(1 if request["is_verified"] else 0)

    return frappe.db.sql(
        f"""
        SELECT
            s.name, s.public_id, s.user, s.business_category, s.seller_type,
            s.location_name, s.locality, s.region, s.country_code,
            s.latitude, s.longitude,
            CASE WHEN v.status = 'Approved' THEN 1 ELSE 0 END AS is_verified,
            v.verification_type, v.status AS verification_status
        FROM `tabAOS Seller` s
        INNER JOIN `tabUser` u ON u.name = s.user
        INNER JOIN `tabAOS Profile` p ON p.user = s.user
        LEFT JOIN `tabAOS Verification Request` v ON v.user = s.user
        WHERE {' AND '.join(conditions)}
        ORDER BY
            CASE WHEN v.status = 'Approved' THEN 1 ELSE 0 END DESC,
            s.creation DESC,
            s.name ASC
        LIMIT %s
        """,
        (*params, max(1, int(maximum_rows))),
        as_dict=True,
    )
