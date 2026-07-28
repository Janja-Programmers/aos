"""Harden Reviews persistence, lifecycle, uniqueness, and aggregate indexes.

Legacy duplicate reviews are never silently deleted. The oldest row remains the
canonical review; later duplicates are withdrawn, retain their audit history,
and receive a null review_key so the new uniqueness constraint can be applied.
"""

from __future__ import annotations

import hashlib

import frappe
from frappe.utils import now_datetime

_BATCH_SIZE = 250
_INDEXES: dict[str, tuple[str, list[str]]] = {
    "idx_aos_review_public_ad": ("AOS Review", ["ad", "status", "creation", "name"]),
    "idx_aos_review_public_helpful": ("AOS Review", ["ad", "status", "like_count", "creation", "name"]),
    "idx_aos_review_reviewer_history": ("AOS Review", ["reviewer", "status", "creation", "name"]),
    "idx_aos_review_moderation": ("AOS Review", ["status", "modified", "name"]),
    "idx_aos_review_reaction_review": ("AOS Review Reaction", ["review", "reaction", "name"]),
    "idx_aos_review_report_backlog": ("AOS Review Report", ["status", "creation", "name"]),
    "idx_aos_review_report_target": ("AOS Review Report", ["review", "status", "creation", "name"]),
}
_UNIQUES: dict[str, tuple[str, list[str]]] = {
    "uq_aos_review_key": ("AOS Review", ["review_key"]),
    "uq_aos_review_reaction_user": ("AOS Review Reaction", ["review", "user"]),
    "uq_aos_review_report_user": ("AOS Review Report", ["review", "reported_by"]),
}


def execute() -> None:
    _reload_review_doctypes()
    if not frappe.db.table_exists("AOS Review"):
        return

    duplicate_reviews = _reconcile_duplicate_reviews()
    duplicate_reactions = _deduplicate_rows("AOS Review Reaction", ["review", "user"])
    duplicate_reports = _deduplicate_rows("AOS Review Report", ["review", "reported_by"])
    _backfill_review_metadata()
    _backfill_aggregates()

    for name, (doctype, fields) in _INDEXES.items():
        if _supports(doctype, fields) and not _index_exists(doctype, name):
            frappe.db.add_index(doctype, fields, index_name=name)
    for name, (doctype, fields) in _UNIQUES.items():
        if _supports(doctype, fields) and not _index_exists(doctype, name):
            frappe.db.add_unique(doctype, fields, constraint_name=name)

    frappe.logger("aos.reviews", allow_site=True).info(
        "reviews_schema_hardening_complete duplicate_reviews_withdrawn=%s "
        "duplicate_reactions_removed=%s duplicate_reports_removed=%s",
        duplicate_reviews,
        duplicate_reactions,
        duplicate_reports,
    )


def _reload_review_doctypes() -> None:
    for doctype in ("aos_review", "aos_review_image", "aos_review_reaction", "aos_review_report"):
        frappe.reload_doc("aos", "doctype", doctype, force=True)


def _review_key(reviewer: str, ad_id: str) -> str:
    material = f"{str(reviewer or '').strip()}\x1f{str(ad_id or '').strip()}".encode("utf-8")
    return "review:" + hashlib.sha256(material).hexdigest()


def _reconcile_duplicate_reviews() -> int:
    if not _supports("AOS Review", ["reviewer", "ad", "review_key", "status", "withdrawn_on"]):
        return 0
    withdrawn = 0
    while True:
        groups = frappe.db.sql(
            """
            SELECT reviewer, ad
            FROM `tabAOS Review`
            WHERE COALESCE(reviewer, '') != ''
              AND COALESCE(ad, '') != ''
              AND status != 'Withdrawn'
            GROUP BY reviewer, ad
            HAVING COUNT(*) > 1
            ORDER BY reviewer, ad
            LIMIT %s
            """,
            (_BATCH_SIZE,),
            as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            rows = frappe.db.sql(
                """
                SELECT name, status, review_key
                FROM `tabAOS Review`
                WHERE reviewer = %s AND ad = %s AND status != 'Withdrawn'
                ORDER BY creation ASC, name ASC
                """,
                (group.reviewer, group.ad),
                as_dict=True,
            )
            canonical = rows[0]
            frappe.db.sql(
                "UPDATE `tabAOS Review` SET review_key = %s WHERE name = %s",
                (_review_key(group.reviewer, group.ad), canonical.name),
            )
            duplicates = [row.name for row in rows[1:]]
            if not duplicates:
                continue
            placeholders = ",".join(["%s"] * len(duplicates))
            frappe.db.sql(
                f"""
                UPDATE `tabAOS Review`
                SET status = 'Withdrawn', review_key = NULL,
                    withdrawn_on = COALESCE(withdrawn_on, %s),
                    review_notes = CASE
                        WHEN COALESCE(review_notes, '') = '' THEN 'Legacy duplicate withdrawn during review hardening.'
                        ELSE review_notes
                    END
                WHERE name IN ({placeholders})
                """,
                (now_datetime(), *duplicates),
            )
            withdrawn += len(duplicates)
    return withdrawn


def _backfill_review_metadata() -> None:
    start_after = ""
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, reviewer, ad
            FROM `tabAOS Review`
            WHERE name > %s
              AND COALESCE(review_key, '') = ''
              AND status != 'Withdrawn'
              AND COALESCE(reviewer, '') != ''
              AND COALESCE(ad, '') != ''
            ORDER BY name
            LIMIT %s
            """,
            (start_after, _BATCH_SIZE),
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            frappe.db.set_value(
                "AOS Review",
                row.name,
                {
                    "review_key": _review_key(row.reviewer, row.ad),
                    "eligibility_basis": "communication",
                    "moderation_generation": 1,
                    "edit_count": 0,
                },
                update_modified=False,
            )
        start_after = rows[-1].name
    frappe.db.sql(
        """
        UPDATE `tabAOS Review`
        SET eligibility_basis = COALESCE(NULLIF(eligibility_basis, ''), 'communication'),
            moderation_generation = CASE WHEN moderation_generation < 1 THEN 1 ELSE moderation_generation END,
            edit_count = CASE WHEN edit_count < 0 THEN 0 ELSE edit_count END
        """
    )


def _deduplicate_rows(doctype: str, fields: list[str]) -> int:
    if not _supports(doctype, fields):
        return 0
    table = f"`tab{doctype}`"
    field_sql = ", ".join(f"`{field}`" for field in fields)
    removed = 0
    while True:
        groups = frappe.db.sql(
            f"""
            SELECT {field_sql}
            FROM {table}
            GROUP BY {field_sql}
            HAVING COUNT(*) > 1
            ORDER BY {field_sql}
            LIMIT %s
            """,
            (_BATCH_SIZE,),
            as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            where = " AND ".join(f"`{field}` <=> %s" for field in fields)
            values = tuple(group[field] for field in fields)
            rows = frappe.db.sql(
                f"SELECT name FROM {table} WHERE {where} ORDER BY creation ASC, name ASC",
                values,
                as_dict=True,
            )
            for duplicate in rows[1:]:
                frappe.db.sql(f"DELETE FROM {table} WHERE name = %s", (duplicate.name,))
                removed += 1
    return removed


def _backfill_aggregates() -> None:
    _backfill_ad_aggregates()
    _backfill_seller_aggregates()


def _backfill_ad_aggregates() -> None:
    if not _supports("AOS Ad", ["average_rating", "total_reviews"]):
        return
    start_after = ""
    while True:
        ads = frappe.db.sql(
            """
            SELECT name, average_rating, total_reviews
            FROM `tabAOS Ad`
            WHERE name > %s
            ORDER BY name
            LIMIT %s
            """,
            (start_after, _BATCH_SIZE),
            as_dict=True,
        )
        if not ads:
            break
        for ad in ads:
            aggregate = frappe.db.sql(
                """
                SELECT COALESCE(AVG(rating), 0) AS average_rating,
                       COUNT(name) AS total_reviews
                FROM `tabAOS Review`
                WHERE ad = %s AND status = 'Approved'
                """,
                (ad.name,),
                as_dict=True,
            )[0]
            average = round(float(aggregate.average_rating or 0), 2)
            total = max(0, int(aggregate.total_reviews or 0))
            if (round(float(ad.average_rating or 0), 2), int(ad.total_reviews or 0)) != (
                average,
                total,
            ):
                frappe.db.set_value(
                    "AOS Ad",
                    ad.name,
                    {"average_rating": average, "total_reviews": total},
                    update_modified=False,
                )
        start_after = ads[-1].name


def _backfill_seller_aggregates() -> None:
    if not _supports("AOS Seller", ["rating", "total_reviews"]):
        return
    start_after = ""
    while True:
        sellers = frappe.db.sql(
            """
            SELECT name, rating, total_reviews
            FROM `tabAOS Seller`
            WHERE name > %s
            ORDER BY name
            LIMIT %s
            """,
            (start_after, _BATCH_SIZE),
            as_dict=True,
        )
        if not sellers:
            break
        for seller in sellers:
            aggregate = frappe.db.sql(
                """
                SELECT COALESCE(AVG(r.rating), 0) AS average_rating,
                       COUNT(r.name) AS total_reviews
                FROM `tabAOS Review` r
                INNER JOIN `tabAOS Ad` a ON a.name = r.ad
                WHERE a.seller = %s AND r.status = 'Approved'
                """,
                (seller.name,),
                as_dict=True,
            )[0]
            average = round(float(aggregate.average_rating or 0), 2)
            total = max(0, int(aggregate.total_reviews or 0))
            if (round(float(seller.rating or 0), 2), int(seller.total_reviews or 0)) != (
                average,
                total,
            ):
                frappe.db.set_value(
                    "AOS Seller",
                    seller.name,
                    {"rating": average, "total_reviews": total},
                    update_modified=False,
                )
        start_after = sellers[-1].name


def _supports(doctype: str, fields: list[str]) -> bool:
    return bool(
        frappe.db.table_exists(doctype)
        and all(frappe.db.has_column(doctype, field) for field in fields)
    )


def _index_exists(doctype: str, name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1 FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
            LIMIT 1
            """,
            (f"tab{doctype}", name),
        )
    )
