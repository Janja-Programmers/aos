"""List Approved Reviews for an Ad."""

from __future__ import annotations

from typing import Any, Dict, List

import frappe

from aos.api.shared.auth import optional_active_user

from aos.api.shared.responses import fail, ok
from aos.api.shared.user_display import get_user_display_map
from aos.api.shared.formatters import humanize_count


ALLOWED_SORTS = {"newest", "helpful", "rating_high", "rating_low"}


def list_reviews_impl(**kwargs):
    """List approved reviews for a specific Ad with summary & distribution."""

    ad = str(kwargs.get("ad") or "").strip()
    sort = str(kwargs.get("sort") or "newest").strip()
    rating_filter = kwargs.get("rating")

    try:
        limit = max(1, min(int(kwargs.get("limit") or 20), 50))
        offset = max(0, int(kwargs.get("offset") or 0))
    except Exception:
        return fail("Invalid pagination values.", code="VALIDATION_ERROR")

    if not ad:
        return fail("Ad is required.", code="VALIDATION_ERROR")

    if sort not in ALLOWED_SORTS:
        return fail(
            "Invalid sort.",
            code="VALIDATION_ERROR",
            data={"allowed": sorted(ALLOWED_SORTS)},
        )

    # Validate rating filter
    if rating_filter is not None:
        try:
            rating_filter = int(rating_filter)

            if rating_filter not in (1, 2, 3, 4, 5):
                raise ValueError

        except Exception:
            return fail("Invalid rating filter.", code="VALIDATION_ERROR")

    ad_doc = frappe.db.get_value(
        "AOS Ad",
        ad,
        ["name", "status", "average_rating", "total_reviews"],
        as_dict=True,
    )

    if not ad_doc or ad_doc.status != "Active":
        return fail("Ad not found.", code="NOT_FOUND")

    try:
        # Rating distribution (5★–1★)
        distribution_rows = frappe.db.sql(
            """
            SELECT rating, COUNT(*) as count
            FROM `tabAOS Review`
            WHERE ad=%s AND status='Approved'
            GROUP BY rating
            """,
            (ad,),
            as_dict=True,
        )

        distribution: Dict[str, int] = {str(i): 0 for i in range(1, 6)}

        for row in distribution_rows:
            distribution[str(int(row["rating"]))] = int(row["count"])

        # Filters
        filters: Dict[str, Any] = {
            "ad": ad,
            "status": "Approved",
        }

        if rating_filter is not None:
            filters["rating"] = rating_filter

        # Sorting
        if sort == "newest":
            order_by = "creation desc"
        elif sort == "helpful":
            order_by = "like_count desc, creation desc"
        elif sort == "rating_high":
            order_by = "rating desc, creation desc"
        else:  # rating_low
            order_by = "rating asc, creation desc"

        # Fetch Reviews
        reviews = frappe.get_all(
            "AOS Review",
            filters=filters,
            fields=[
                "name",
                "rating",
                "title",
                "comment",
                "reviewer",
                "creation",
                "like_count",
                "dislike_count",
            ],
            order_by=order_by,
            limit=limit,
            start=offset,
        )

        total = frappe.db.count("AOS Review", filters)

        # Prepare review IDs
        review_names = [r["name"] for r in reviews]

        # User Reaction
        current_user = optional_active_user()
        user_reactions_map: Dict[str, str] = {}

        if current_user and review_names:
            reactions = frappe.get_all(
                "AOS Review Reaction",
                filters={
                    "review": ["in", review_names],
                    "user": current_user,
                },
                fields=["review", "reaction"],
            )

            for r in reactions:
                user_reactions_map[r["review"]] = r["reaction"]

        # Images
        images_map: Dict[str, List[str]] = {name: [] for name in review_names}

        if review_names:
            image_rows = frappe.get_all(
                "AOS Review Image",
                filters={
                    "parenttype": "AOS Review",
                    "parent": ["in", review_names],
                },
                fields=["parent", "image"],
            )

            for row in image_rows:
                images_map.setdefault(row["parent"], []).append(row["image"])

        # Reviewer Info
        reviewer_emails = list({r["reviewer"] for r in reviews})
        user_map: Dict[str, Dict[str, Any]] = get_user_display_map(reviewer_emails)

        # Format Response
        formatted_reviews = []

        for r in reviews:
            reviewer_info = user_map.get(r["reviewer"], {})

            formatted_reviews.append(
                {
                    "id": r["name"],
                    "rating": r["rating"],
                    "title": r["title"],
                    "comment": r["comment"],
                    "created_at": r["creation"],
                    "like_count": int(r["like_count"] or 0),
                    "like_count_display": humanize_count(r["like_count"] or 0),
                    "dislike_count": int(r["dislike_count"] or 0),
                    "dislike_count_display": humanize_count(r["dislike_count"] or 0),
                    "user_reaction": user_reactions_map.get(r["name"]),
                    "reviewer": {
                        "full_name": reviewer_info.get("display_name", ""),
                        "avatar": reviewer_info.get("avatar", ""),
                        "is_deleted": bool(reviewer_info.get("is_deleted")),
                        "is_live": bool(reviewer_info.get("is_live")) if not bool(reviewer_info.get("is_deleted")) else False,
                        "live_id": reviewer_info.get("live_id") if not bool(reviewer_info.get("is_deleted")) else None,
                        "live_status": reviewer_info.get("live_status") if not bool(reviewer_info.get("is_deleted")) else None,
                    },
                    "images": images_map.get(r["name"], []),
                }
            )

        return ok(
            "Reviews fetched.",
            data={
                "summary": {
                    "average_rating": float(ad_doc.average_rating or 0),
                    "total_reviews": int(ad_doc.total_reviews or 0),
                    "total_reviews_display": humanize_count(ad_doc.total_reviews or 0),
                    "distribution": distribution,
                    "distribution_display": {
                        rating: humanize_count(count)
                        for rating, count in distribution.items()
                    },
                },
                "reviews": formatted_reviews,
                "pagination": {
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "has_more": offset + limit < total,
                },
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS List Reviews Failed")
        return fail("Failed to fetch reviews.", code="INTERNAL_ERROR")
