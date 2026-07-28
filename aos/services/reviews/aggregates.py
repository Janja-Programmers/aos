"""Canonical review aggregate calculation and bounded reconciliation."""

from __future__ import annotations

from typing import Any

import frappe

from .constants import STATUS_APPROVED
from .observability import review_log


def recompute_review_aggregates(*, ad_id: str, lock_target: bool = False) -> dict[str, Any]:
    """Recompute Ad and Seller review aggregates from approved source rows.

    The caller owns the transaction. No commit is performed here.
    """

    ad_id = str(ad_id or "").strip()
    if not ad_id or not frappe.db.exists("AOS Ad", ad_id):
        return {"ad": ad_id or None, "seller": None, "average_rating": 0.0, "total_reviews": 0}
    if lock_target:
        frappe.db.sql("SELECT name FROM `tabAOS Ad` WHERE name = %s FOR UPDATE", (ad_id,))

    row = frappe.db.sql(
        """
        SELECT COALESCE(AVG(rating), 0) AS average_rating,
               COUNT(name) AS total_reviews
        FROM `tabAOS Review`
        WHERE ad = %s AND status = %s
        """,
        (ad_id, STATUS_APPROVED),
        as_dict=True,
    )[0]
    average = round(float(row.average_rating or 0), 2)
    total = max(0, int(row.total_reviews or 0))
    frappe.db.set_value(
        "AOS Ad",
        ad_id,
        {"average_rating": average, "total_reviews": total},
        update_modified=False,
    )

    seller_id = frappe.db.get_value("AOS Ad", ad_id, "seller")
    seller_average = 0.0
    seller_total = 0
    if seller_id:
        if lock_target:
            frappe.db.sql("SELECT name FROM `tabAOS Seller` WHERE name = %s FOR UPDATE", (seller_id,))
        seller_row = frappe.db.sql(
            """
            SELECT COALESCE(AVG(r.rating), 0) AS average_rating,
                   COUNT(r.name) AS total_reviews
            FROM `tabAOS Review` r
            INNER JOIN `tabAOS Ad` a ON a.name = r.ad
            WHERE a.seller = %s AND r.status = %s
            """,
            (seller_id, STATUS_APPROVED),
            as_dict=True,
        )[0]
        seller_average = round(float(seller_row.average_rating or 0), 2)
        seller_total = max(0, int(seller_row.total_reviews or 0))
        frappe.db.set_value(
            "AOS Seller",
            seller_id,
            {"rating": seller_average, "total_reviews": seller_total},
            update_modified=False,
        )

    review_log(
        "review.aggregate.updated",
        review_id=ad_id,
        operation="recompute",
        status=STATUS_APPROVED,
        count=total,
    )
    return {
        "ad": ad_id,
        "seller": seller_id,
        "average_rating": average,
        "total_reviews": total,
        "seller_average_rating": seller_average,
        "seller_total_reviews": seller_total,
    }



def recompute_review_reaction_counts(*, review_id: str, lock_review: bool = False) -> dict[str, int]:
    """Rebuild Like/Dislike counters from canonical reaction rows.

    The caller owns the transaction. This helper is shared by the API, DocType
    hooks, account deletion, and reconciliation paths so counters cannot drift
    through duplicated implementations.
    """

    review_id = str(review_id or "").strip()
    if not review_id or not frappe.db.exists("AOS Review", review_id):
        return {"Like": 0, "Dislike": 0}
    if lock_review:
        frappe.db.sql(
            "SELECT name FROM `tabAOS Review` WHERE name = %s FOR UPDATE",
            (review_id,),
        )
    rows = frappe.db.sql(
        """
        SELECT reaction, COUNT(*) AS count
        FROM `tabAOS Review Reaction`
        WHERE review = %s
        GROUP BY reaction
        """,
        (review_id,),
        as_dict=True,
    )
    counts = {"Like": 0, "Dislike": 0}
    for row in rows:
        reaction = str(row.reaction or "")
        if reaction in counts:
            counts[reaction] = max(0, int(row.count or 0))
    frappe.db.set_value(
        "AOS Review",
        review_id,
        {"like_count": counts["Like"], "dislike_count": counts["Dislike"]},
        update_modified=False,
    )
    return counts

def rating_distribution(ad_id: str) -> dict[str, int]:
    distribution = {str(value): 0 for value in range(1, 6)}
    rows = frappe.db.sql(
        """
        SELECT rating, COUNT(*) AS count
        FROM `tabAOS Review`
        WHERE ad = %s AND status = %s
        GROUP BY rating
        ORDER BY rating
        """,
        (ad_id, STATUS_APPROVED),
        as_dict=True,
    )
    for row in rows:
        rating = int(row.rating or 0)
        if 1 <= rating <= 5:
            distribution[str(rating)] = max(0, int(row.count or 0))
    return distribution


def reconcile_review_aggregates(
    *,
    dry_run: bool = True,
    batch_size: int = 100,
    start_after: str | None = None,
) -> dict[str, Any]:
    """Inspect or repair aggregate drift in a bounded, rerunnable batch."""

    batch_size = max(1, min(int(batch_size or 100), 500))
    filters: dict[str, Any] = {}
    if start_after:
        filters["name"] = [">", str(start_after)]
    ads = frappe.get_all(
        "AOS Ad",
        filters=filters,
        fields=["name", "average_rating", "total_reviews"],
        order_by="name asc",
        limit=batch_size,
    )
    drifted: list[dict[str, Any]] = []
    updated = 0
    for ad in ads:
        canonical = frappe.db.sql(
            """
            SELECT COALESCE(AVG(rating), 0) AS average_rating, COUNT(name) AS total_reviews
            FROM `tabAOS Review`
            WHERE ad = %s AND status = %s
            """,
            (ad.name, STATUS_APPROVED),
            as_dict=True,
        )[0]
        expected_average = round(float(canonical.average_rating or 0), 2)
        expected_total = max(0, int(canonical.total_reviews or 0))
        current_average = round(float(ad.average_rating or 0), 2)
        current_total = max(0, int(ad.total_reviews or 0))
        if (expected_average, expected_total) == (current_average, current_total):
            continue
        drifted.append(
            {
                "ad": ad.name,
                "current": {"average_rating": current_average, "total_reviews": current_total},
                "expected": {"average_rating": expected_average, "total_reviews": expected_total},
            }
        )
        if not dry_run:
            recompute_review_aggregates(ad_id=ad.name, lock_target=True)
            updated += 1
    review_log(
        "review.aggregate.reconciled",
        operation="dry_run" if dry_run else "repair",
        outcome="success",
        count=len(drifted),
    )
    return {
        "dry_run": bool(dry_run),
        "scanned": len(ads),
        "drift_count": len(drifted),
        "updated": updated,
        "drift": drifted,
        "next_start_after": ads[-1].name if len(ads) == batch_size else None,
    }


def reconcile_seller_review_aggregates(
    *,
    dry_run: bool = True,
    batch_size: int = 100,
    start_after: str | None = None,
) -> dict[str, Any]:
    """Inspect or repair Seller rating drift in a bounded, rerunnable batch."""

    batch_size = max(1, min(int(batch_size or 100), 500))
    filters: dict[str, Any] = {}
    if start_after:
        filters["name"] = [">", str(start_after)]
    sellers = frappe.get_all(
        "AOS Seller",
        filters=filters,
        fields=["name", "rating", "total_reviews"],
        order_by="name asc",
        limit=batch_size,
    )
    drifted: list[dict[str, Any]] = []
    updated = 0
    for seller in sellers:
        canonical = frappe.db.sql(
            """
            SELECT COALESCE(AVG(r.rating), 0) AS average_rating,
                   COUNT(r.name) AS total_reviews
            FROM `tabAOS Review` r
            INNER JOIN `tabAOS Ad` a ON a.name = r.ad
            WHERE a.seller = %s AND r.status = %s
            """,
            (seller.name, STATUS_APPROVED),
            as_dict=True,
        )[0]
        expected_average = round(float(canonical.average_rating or 0), 2)
        expected_total = max(0, int(canonical.total_reviews or 0))
        current_average = round(float(seller.rating or 0), 2)
        current_total = max(0, int(seller.total_reviews or 0))
        if (expected_average, expected_total) == (current_average, current_total):
            continue
        drifted.append(
            {
                "seller": seller.name,
                "current": {
                    "average_rating": current_average,
                    "total_reviews": current_total,
                },
                "expected": {
                    "average_rating": expected_average,
                    "total_reviews": expected_total,
                },
            }
        )
        if not dry_run:
            frappe.db.sql(
                "SELECT name FROM `tabAOS Seller` WHERE name = %s FOR UPDATE",
                (seller.name,),
            )
            frappe.db.set_value(
                "AOS Seller",
                seller.name,
                {"rating": expected_average, "total_reviews": expected_total},
                update_modified=False,
            )
            updated += 1
    review_log(
        "review.aggregate.reconciled",
        target_type="seller",
        operation="dry_run" if dry_run else "repair",
        outcome="success",
        count=len(drifted),
    )
    return {
        "dry_run": bool(dry_run),
        "scanned": len(sellers),
        "drift_count": len(drifted),
        "updated": updated,
        "drift": drifted,
        "next_start_after": sellers[-1].name if len(sellers) == batch_size else None,
    }
