"""Reviews aggregate projections and bounded reconciliation."""

from __future__ import annotations

from typing import Any

import frappe

from .constants import STATUS_APPROVED
from .observability import review_log


def _contribution(status: str | None, rating: Any) -> tuple[int, int]:
    if str(status or "") != STATUS_APPROVED:
        return 0, 0
    value = int(float(rating or 0))
    return value, 1


def apply_review_aggregate_delta(
    *,
    ad_id: str,
    old_status: str | None,
    old_rating: Any,
    new_status: str | None,
    new_rating: Any,
) -> dict[str, Any]:
    """Apply one Review lifecycle delta to Ad and Seller projections.

    Approved Review rows are authoritative. Projection rows are locked in a
    consistent Ad -> Seller order and updated from exact integer rating sums,
    avoiding full aggregate scans on normal create/edit/moderation writes.
    """
    ad_id = str(ad_id or "").strip()
    old_sum, old_count = _contribution(old_status, old_rating)
    new_sum, new_count = _contribution(new_status, new_rating)
    rating_delta = new_sum - old_sum
    count_delta = new_count - old_count
    if not ad_id or (rating_delta == 0 and count_delta == 0):
        return {"changed": False}

    ad_rows = frappe.db.sql(
        """
        SELECT name, seller, COALESCE(review_rating_sum, 0) AS review_rating_sum,
               COALESCE(total_reviews, 0) AS total_reviews
        FROM `tabAOS Ad`
        WHERE name = %s
        FOR UPDATE
        """,
        (ad_id,),
        as_dict=True,
    )
    if not ad_rows:
        return {"changed": False}
    ad = ad_rows[0]
    ad_sum = max(0, int(ad.review_rating_sum or 0) + rating_delta)
    ad_total = max(0, int(ad.total_reviews or 0) + count_delta)
    ad_average = round(ad_sum / ad_total, 2) if ad_total else 0.0
    frappe.db.set_value(
        "AOS Ad",
        ad.name,
        {
            "review_rating_sum": ad_sum,
            "total_reviews": ad_total,
            "average_rating": ad_average,
        },
        update_modified=False,
    )

    seller_id = str(ad.seller or "").strip()
    seller_sum = seller_total = 0
    seller_average = 0.0
    if seller_id:
        seller_rows = frappe.db.sql(
            """
            SELECT name, COALESCE(review_rating_sum, 0) AS review_rating_sum,
                   COALESCE(total_reviews, 0) AS total_reviews
            FROM `tabAOS Seller`
            WHERE name = %s
            FOR UPDATE
            """,
            (seller_id,),
            as_dict=True,
        )
        if seller_rows:
            seller = seller_rows[0]
            seller_sum = max(0, int(seller.review_rating_sum or 0) + rating_delta)
            seller_total = max(0, int(seller.total_reviews or 0) + count_delta)
            seller_average = round(seller_sum / seller_total, 2) if seller_total else 0.0
            frappe.db.set_value(
                "AOS Seller",
                seller.name,
                {
                    "review_rating_sum": seller_sum,
                    "total_reviews": seller_total,
                    "rating": seller_average,
                },
                update_modified=False,
            )

    review_log(
        "review.aggregate.updated",
        review_id=ad_id,
        operation="delta",
        status=str(new_status or ""),
        count=ad_total,
    )
    return {
        "changed": True,
        "ad": ad_id,
        "seller": seller_id or None,
        "average_rating": ad_average,
        "total_reviews": ad_total,
        "seller_average_rating": seller_average,
        "seller_total_reviews": seller_total,
    }


def recompute_review_aggregates(*, ad_id: str, lock_target: bool = False) -> dict[str, Any]:
    """Repair Ad and Seller projections from approved Review source rows."""
    ad_id = str(ad_id or "").strip()
    if not ad_id or not frappe.db.exists("AOS Ad", ad_id):
        return {"ad": ad_id or None, "seller": None, "average_rating": 0.0, "total_reviews": 0}
    if lock_target:
        frappe.db.sql("SELECT name FROM `tabAOS Ad` WHERE name = %s FOR UPDATE", (ad_id,))
    row = frappe.db.sql(
        """
        SELECT COALESCE(SUM(rating), 0) AS rating_sum, COUNT(name) AS total_reviews
        FROM `tabAOS Review`
        WHERE ad = %s AND status = %s
        """,
        (ad_id, STATUS_APPROVED),
        as_dict=True,
    )[0]
    rating_sum = max(0, int(row.rating_sum or 0))
    total = max(0, int(row.total_reviews or 0))
    average = round(rating_sum / total, 2) if total else 0.0
    frappe.db.set_value(
        "AOS Ad",
        ad_id,
        {"review_rating_sum": rating_sum, "average_rating": average, "total_reviews": total},
        update_modified=False,
    )

    seller_id = frappe.db.get_value("AOS Ad", ad_id, "seller")
    seller_sum = seller_total = 0
    seller_average = 0.0
    if seller_id:
        if lock_target:
            frappe.db.sql("SELECT name FROM `tabAOS Seller` WHERE name = %s FOR UPDATE", (seller_id,))
        seller_row = frappe.db.sql(
            """
            SELECT COALESCE(SUM(r.rating), 0) AS rating_sum, COUNT(r.name) AS total_reviews
            FROM `tabAOS Review` r
            INNER JOIN `tabAOS Ad` a ON a.name = r.ad
            WHERE a.seller = %s AND r.status = %s
            """,
            (seller_id, STATUS_APPROVED),
            as_dict=True,
        )[0]
        seller_sum = max(0, int(seller_row.rating_sum or 0))
        seller_total = max(0, int(seller_row.total_reviews or 0))
        seller_average = round(seller_sum / seller_total, 2) if seller_total else 0.0
        frappe.db.set_value(
            "AOS Seller",
            seller_id,
            {"review_rating_sum": seller_sum, "rating": seller_average, "total_reviews": seller_total},
            update_modified=False,
        )
    return {
        "ad": ad_id,
        "seller": seller_id,
        "rating_sum": rating_sum,
        "average_rating": average,
        "total_reviews": total,
        "seller_rating_sum": seller_sum,
        "seller_average_rating": seller_average,
        "seller_total_reviews": seller_total,
    }


def apply_reaction_count_delta(*, review_id: str, like_delta: int = 0, dislike_delta: int = 0) -> None:
    """Apply bounded atomic counter deltas after canonical reaction-row mutation."""
    if not like_delta and not dislike_delta:
        return
    frappe.db.sql(
        """
        UPDATE `tabAOS Review`
        SET like_count = GREATEST(COALESCE(like_count, 0) + %s, 0),
            dislike_count = GREATEST(COALESCE(dislike_count, 0) + %s, 0)
        WHERE name = %s
        """,
        (int(like_delta), int(dislike_delta), str(review_id)),
    )


def recompute_review_reaction_counts(*, review_id: str, lock_review: bool = False) -> dict[str, int]:
    """Repair Like/Dislike counters from canonical relationship rows."""
    review_id = str(review_id or "").strip()
    if not review_id or not frappe.db.exists("AOS Review", review_id):
        return {"Like": 0, "Dislike": 0}
    if lock_review:
        frappe.db.sql("SELECT name FROM `tabAOS Review` WHERE name = %s FOR UPDATE", (review_id,))
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
    *, dry_run: bool = True, batch_size: int = 100, start_after: str | None = None
) -> dict[str, Any]:
    """Inspect or repair Ad review aggregate drift in a bounded batch."""
    batch_size = max(1, min(int(batch_size or 100), 500))
    filters: dict[str, Any] = {}
    if start_after:
        filters["name"] = [">", str(start_after)]
    ads = frappe.get_all(
        "AOS Ad",
        filters=filters,
        fields=["name", "review_rating_sum", "average_rating", "total_reviews"],
        order_by="name asc",
        limit=batch_size,
    )
    drifted: list[dict[str, Any]] = []
    updated = 0
    for ad in ads:
        canonical = frappe.db.sql(
            """
            SELECT COALESCE(SUM(rating), 0) AS rating_sum, COUNT(name) AS total_reviews
            FROM `tabAOS Review`
            WHERE ad = %s AND status = %s
            """,
            (ad.name, STATUS_APPROVED),
            as_dict=True,
        )[0]
        expected_sum = max(0, int(canonical.rating_sum or 0))
        expected_total = max(0, int(canonical.total_reviews or 0))
        expected_average = round(expected_sum / expected_total, 2) if expected_total else 0.0
        current = (int(ad.review_rating_sum or 0), round(float(ad.average_rating or 0), 2), int(ad.total_reviews or 0))
        expected = (expected_sum, expected_average, expected_total)
        if current == expected:
            continue
        drifted.append({"ad": ad.name, "current": current, "expected": expected})
        if not dry_run:
            recompute_review_aggregates(ad_id=ad.name, lock_target=True)
            updated += 1
    return {
        "dry_run": bool(dry_run),
        "scanned": len(ads),
        "drift_count": len(drifted),
        "updated": updated,
        "drift": drifted,
        "next_start_after": ads[-1].name if len(ads) == batch_size else None,
    }


def reconcile_seller_review_aggregates(
    *, dry_run: bool = True, batch_size: int = 100, start_after: str | None = None
) -> dict[str, Any]:
    """Inspect or repair Seller review aggregate drift in a bounded batch."""
    batch_size = max(1, min(int(batch_size or 100), 500))
    filters: dict[str, Any] = {}
    if start_after:
        filters["name"] = [">", str(start_after)]
    sellers = frappe.get_all(
        "AOS Seller",
        filters=filters,
        fields=["name", "review_rating_sum", "rating", "total_reviews"],
        order_by="name asc",
        limit=batch_size,
    )
    drifted: list[dict[str, Any]] = []
    updated = 0
    for seller in sellers:
        canonical = frappe.db.sql(
            """
            SELECT COALESCE(SUM(r.rating), 0) AS rating_sum, COUNT(r.name) AS total_reviews
            FROM `tabAOS Review` r
            INNER JOIN `tabAOS Ad` a ON a.name = r.ad
            WHERE a.seller = %s AND r.status = %s
            """,
            (seller.name, STATUS_APPROVED),
            as_dict=True,
        )[0]
        expected_sum = max(0, int(canonical.rating_sum or 0))
        expected_total = max(0, int(canonical.total_reviews or 0))
        expected_average = round(expected_sum / expected_total, 2) if expected_total else 0.0
        current = (int(seller.review_rating_sum or 0), round(float(seller.rating or 0), 2), int(seller.total_reviews or 0))
        expected = (expected_sum, expected_average, expected_total)
        if current == expected:
            continue
        drifted.append({"seller": seller.name, "current": current, "expected": expected})
        if not dry_run:
            frappe.db.sql("SELECT name FROM `tabAOS Seller` WHERE name = %s FOR UPDATE", (seller.name,))
            frappe.db.set_value(
                "AOS Seller",
                seller.name,
                {"review_rating_sum": expected_sum, "rating": expected_average, "total_reviews": expected_total},
                update_modified=False,
            )
            updated += 1
    return {
        "dry_run": bool(dry_run),
        "scanned": len(sellers),
        "drift_count": len(drifted),
        "updated": updated,
        "drift": drifted,
        "next_start_after": sellers[-1].name if len(sellers) == batch_size else None,
    }
