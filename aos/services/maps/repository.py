"""Database access for seller-linked Maps operations."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.blocking import is_blocked_between
from aos.services.sellers.errors import SellerStateError
from aos.services.sellers.identity import resolve_seller_reference

_LOCATION_FIELDS = [
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


def get_seller_location_for_user(user: str) -> dict[str, Any] | None:
    return frappe.db.get_value(
        "AOS Seller",
        {"user": str(user or "").strip()},
        _LOCATION_FIELDS,
        as_dict=True,
    )


def lock_seller_for_location_mutation(user: str):
    rows = frappe.db.sql(
        "SELECT name FROM `tabAOS Seller` WHERE user = %s FOR UPDATE",
        (str(user or "").strip(),),
        as_dict=True,
    )
    if not rows:
        return None
    doc = frappe.get_doc("AOS Seller", rows[0]["name"])
    if str(doc.status or "").strip() != "Active":
        raise SellerStateError(
            "Seller account is not active.",
            code="SELLER_INACTIVE",
            http_status=403,
            data={"status": str(doc.status or "").strip() or None},
        )
    return doc


def get_public_seller_location(
    seller_reference: str,
    *,
    viewer: str | None,
) -> dict[str, Any] | None:
    seller_name = resolve_seller_reference(seller_reference)
    if not seller_name:
        return None
    row = frappe.db.sql(
        """
        SELECT
            s.name,
            s.public_id,
            s.user,
            s.status,
            s.has_location,
            COALESCE(s.location_version, 0) AS location_version,
            s.location_name,
            s.location_instructions,
            s.latitude,
            s.longitude,
            s.display_address,
            s.locality,
            s.region,
            s.country_code,
            s.location_updated_at
        FROM `tabAOS Seller` s
        INNER JOIN `tabUser` u ON u.name = s.user
        INNER JOIN `tabAOS Profile` p ON p.user = s.user
        WHERE s.name = %s
          AND s.status = 'Active'
          AND u.enabled = 1
          AND COALESCE(p.account_status, 'Active') = 'Active'
        LIMIT 1
        """,
        (seller_name,),
        as_dict=True,
    )
    if not row:
        return None
    seller = row[0]
    clean_viewer = str(viewer or "").strip()
    if (
        clean_viewer
        and clean_viewer != "Guest"
        and clean_viewer != seller.get("user")
        and is_blocked_between(clean_viewer, seller.get("user"))
    ):
        return None
    return seller


def list_seller_map_rows(
    request: dict[str, Any],
    *,
    viewer: str | None,
    maximum_rows: int,
) -> list[dict[str, Any]]:
    conditions = [
        "s.status = 'Active'",
        "u.enabled = 1",
        "COALESCE(p.account_status, 'Active') = 'Active'",
        "COALESCE(s.has_location, 0) = 1",
        "s.latitude IS NOT NULL",
        "s.longitude IS NOT NULL",
        "s.latitude BETWEEN %s AND %s",
        "s.longitude BETWEEN %s AND %s",
    ]
    params: list[Any] = [
        request["south"],
        request["north"],
        request["west"],
        request["east"],
    ]
    clean_viewer = str(viewer or "").strip()
    if clean_viewer and clean_viewer != "Guest":
        conditions.append("s.user != %s")
        params.append(clean_viewer)
        conditions.append(
            """
            NOT EXISTS (
                SELECT 1
                FROM `tabAOS User Block` b
                WHERE b.status = 'Active'
                  AND (
                    (b.blocker_user = %s AND b.blocked_user = s.user)
                    OR (b.blocked_user = %s AND b.blocker_user = s.user)
                  )
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
        conditions.append("COALESCE(p.is_verified, 0) = %s")
        params.append(1 if request["is_verified"] else 0)
    where_clause = " AND ".join(conditions)
    return frappe.db.sql(
        f"""
        SELECT
            s.name,
            s.public_id,
            s.user,
            s.business_category,
            s.seller_type,
            s.location_name,
            s.locality,
            s.region,
            s.country_code,
            s.latitude,
            s.longitude,
            COALESCE(p.is_verified, 0) AS is_verified,
            u.full_name,
            u.user_image
        FROM `tabAOS Seller` s
        INNER JOIN `tabUser` u ON u.name = s.user
        INNER JOIN `tabAOS Profile` p ON p.user = s.user
        WHERE {where_clause}
        ORDER BY
            COALESCE(p.is_verified, 0) DESC,
            s.creation DESC,
            s.name ASC
        LIMIT %s
        """,
        (*params, max(1, int(maximum_rows))),
        as_dict=True,
    )
