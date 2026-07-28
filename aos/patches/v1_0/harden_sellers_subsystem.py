"""Harden Seller identity, lifecycle metadata, counters, and query indexes.

The patch is additive, bounded, idempotent, and deliberately does not commit.
Internal Seller document names remain unchanged for foreign-key compatibility;
an opaque public ID is backfilled for API use.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re

import frappe
from frappe.utils import now_datetime

_BATCH_SIZE = 250
_INDEXES: dict[str, list[str]] = {
    "idx_aos_seller_public_discovery": ["status", "seller_type", "rating", "creation", "name"],
    "idx_aos_seller_category_discovery": ["status", "business_category", "creation", "name"],
    "idx_aos_seller_ad_discovery": ["status", "total_ads", "creation", "name"],
    "idx_aos_seller_review_discovery": ["status", "total_reviews", "rating", "name"],
}
_UNIQUE_INDEX = "uq_aos_seller_public_id"
_VALID_STATUSES = {"Active", "Suspended", "Deleted"}
_VALID_TYPES = {"Individual", "Business"}
_PUBLIC_ID_RE = re.compile(r"^SELLER-[A-Z2-7]{20}$")


def execute() -> None:
    frappe.reload_doc("aos", "doctype", "aos_seller", force=True)
    if not frappe.db.table_exists("AOS Seller"):
        return

    _backfill_rows()
    _reconcile_active_ad_counts()
    for name, fields in _INDEXES.items():
        if _supports(fields) and not _index_exists(name):
            frappe.db.add_index("AOS Seller", fields, index_name=name)
    if _supports(["public_id"]) and not _index_exists(_UNIQUE_INDEX):
        frappe.db.add_unique("AOS Seller", ["public_id"], constraint_name=_UNIQUE_INDEX)


def _site_secret() -> bytes:
    value = ""
    try:
        value = str(getattr(frappe.local, "conf", {}).get("encryption_key") or "")
    except Exception:
        value = ""
    if not value:
        try:
            value = str((frappe.get_site_config() or {}).get("encryption_key") or "")
        except Exception:
            value = ""
    if not value:
        value = "aos:seller-public-id-migration"
    return value.encode("utf-8")


def _public_id(name: str, *, attempt: int = 0) -> str:
    material = f"{str(name or '').strip()}:{max(0, int(attempt))}"
    digest = hmac.new(_site_secret(), material.encode("utf-8"), hashlib.sha256).digest()
    token = base64.b32encode(digest[:12]).decode("ascii").rstrip("=")
    return f"SELLER-{token}"


def _canonical_public_id(name: str, current: object) -> str:
    candidate = str(current or "").strip().upper()
    if _PUBLIC_ID_RE.fullmatch(candidate):
        owner = frappe.db.sql(
            "SELECT MIN(name) FROM `tabAOS Seller` WHERE public_id = %s",
            (candidate,),
        )
        if owner and str(owner[0][0] or "") == str(name):
            return candidate

    for attempt in range(16):
        generated = _public_id(name, attempt=attempt)
        owner = frappe.db.get_value("AOS Seller", {"public_id": generated}, "name")
        if not owner or str(owner) == str(name):
            return generated
    raise RuntimeError("Unable to allocate a unique public Seller ID during migration")


def _backfill_rows() -> None:
    required = [
        "public_id",
        "status",
        "seller_type",
        "status_changed_at",
        "status_reason_code",
        "status_source",
        "storefront_version",
    ]
    if not _supports(required):
        return
    start_after = ""
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, public_id, status, seller_type, status_changed_at,
                   status_reason_code, status_source, storefront_version,
                   creation, modified
            FROM `tabAOS Seller`
            WHERE name > %s
            ORDER BY name
            LIMIT %s
            """,
            (start_after, _BATCH_SIZE),
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            status = str(row.status or "").strip()
            seller_type = str(row.seller_type or "").strip()
            values = {
                "public_id": _canonical_public_id(row.name, row.public_id),
                "status": status if status in _VALID_STATUSES else "Suspended",
                "seller_type": seller_type if seller_type in _VALID_TYPES else "Individual",
                "status_changed_at": row.status_changed_at or row.modified or row.creation or now_datetime(),
                "status_reason_code": str(row.status_reason_code or "").strip()
                or ("LEGACY_STATUS" if status in _VALID_STATUSES else "INVALID_LEGACY_STATUS"),
                "status_source": str(row.status_source or "").strip() or "seller_hardening_migration",
                "storefront_version": max(0, int(row.storefront_version or 0)),
            }
            frappe.db.set_value("AOS Seller", row.name, values, update_modified=False)
        start_after = rows[-1].name

    metric_updates: list[str] = []
    for field in (
        "total_ads",
        "total_reviews",
        "chat_response_time_seconds",
        "chat_response_sample_size",
        "chat_response_requests",
    ):
        if frappe.db.has_column("AOS Seller", field):
            metric_updates.append(f"`{field}` = CASE WHEN COALESCE(`{field}`, 0) < 0 THEN 0 ELSE COALESCE(`{field}`, 0) END")
    if frappe.db.has_column("AOS Seller", "rating"):
        metric_updates.append("rating = LEAST(5, GREATEST(0, COALESCE(rating, 0)))")
    if frappe.db.has_column("AOS Seller", "chat_response_rate"):
        metric_updates.append("chat_response_rate = LEAST(100, GREATEST(0, COALESCE(chat_response_rate, 0)))")
    if metric_updates:
        frappe.db.sql(f"UPDATE `tabAOS Seller` SET {', '.join(metric_updates)}")


def _reconcile_active_ad_counts() -> None:
    if not (
        frappe.db.table_exists("AOS Ad")
        and _supports(["total_ads"])
        and frappe.db.has_column("AOS Ad", "seller")
        and frappe.db.has_column("AOS Ad", "status")
    ):
        return
    frappe.db.sql(
        """
        UPDATE `tabAOS Seller` s
        LEFT JOIN (
            SELECT seller, COUNT(*) AS active_count
            FROM `tabAOS Ad`
            WHERE status = 'Active'
            GROUP BY seller
        ) a ON a.seller = s.name
        SET s.total_ads = COALESCE(a.active_count, 0)
        """
    )


def _supports(fields: list[str]) -> bool:
    return bool(
        frappe.db.table_exists("AOS Seller")
        and all(frappe.db.has_column("AOS Seller", field) for field in fields)
    )


def _index_exists(name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1 FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'tabAOS Seller'
              AND INDEX_NAME = %s
            LIMIT 1
            """,
            (name,),
        )
    )
