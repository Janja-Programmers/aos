"""Cohesive Reviews application service.

The current AOS business model permits one user-to-ad review after the buyer and
seller have communicated through AOS chat/call messaging. No Orders or Bookings
model currently grants review eligibility, so the server derives eligibility
from the canonical conversation/message records and never trusts client flags.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.api.shared.blocking import is_blocked_between
from aos.api.shared.db import is_duplicate_entry_error
from aos.api.shared.formatters import humanize_count
from aos.services.media.media_service import MediaService
from aos.services.sellers.identity import public_seller_id_for_name
from aos.services.moderation_service import enqueue_review_moderation

from .aggregates import (
    rating_distribution,
    recompute_review_reaction_counts,
)
from .constants import (
    ELIGIBILITY_BASIS_COMMUNICATION,
    PUBLIC_SORTS,
    REACTION_DOCTYPE,
    REPORT_DOCTYPE,
    SELF_SORTS,
    STATUS_APPROVED,
    STATUS_HIDDEN,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_WITHDRAWN,
)
from .eligibility import (
    enforce_review_eligibility,
    get_review_eligibility,
    public_eligibility_state,
    resolve_ad_target,
    review_key,
)
from .errors import (
    ReviewConflictError,
    ReviewNotFoundError,
    ReviewPermissionError,
    ReviewStateError,
    ReviewValidationError,
)
from .observability import review_log
from .serializers import serialize_reviews
from .validation import (
    ensure_known_fields,
    normalize_comment,
    normalize_identifier,
    normalize_images,
    normalize_pagination,
    normalize_rating,
    normalize_rating_filter,
    normalize_reaction,
    normalize_report_details,
    normalize_report_reason,
    normalize_title,
)


class ReviewService:
    """Review use-cases with explicit transaction ownership by the caller."""

    CREATE_FIELDS = frozenset({"ad", "ad_id", "rating", "title", "comment", "images", "review_images"})
    UPDATE_FIELDS = frozenset({"review", "review_id", "rating", "title", "comment", "images", "review_images"})
    ID_FIELDS = frozenset({"review", "review_id"})
    LIST_FIELDS = frozenset({"ad", "ad_id", "sort", "rating", "limit", "offset"})
    PRIVATE_LIST_FIELDS = frozenset({"sort", "status", "rating", "limit", "offset"})
    REACTION_FIELDS = frozenset({"review", "review_id", "reaction"})
    REPORT_FIELDS = frozenset({"review", "review_id", "reason", "details"})

    def viewer_state(self, *, ad_id: Any, viewer: str | None) -> dict[str, Any]:
        ad = normalize_identifier(ad_id, field="ad", max_length=140)
        state = get_review_eligibility(ad_id=ad, reviewer=viewer)
        review_log(
            "review.eligibility.checked",
            review_id=state.get("existing_review_id") or ad,
            outcome="success" if state.get("can_review") else "rejected",
            operation=str(state.get("reason") or "allowed")[:32],
        )
        return public_eligibility_state(state)

    def create(
        self,
        *,
        user: str,
        payload: dict[str, Any],
        moderation_enqueue: Callable[..., Any] = enqueue_review_moderation,
    ) -> dict[str, Any]:
        ensure_known_fields(payload, self.CREATE_FIELDS)
        ad_id = normalize_identifier(payload.get("ad_id") or payload.get("ad"), field="ad")
        rating = normalize_rating(payload.get("rating"))
        title = normalize_title(payload.get("title"))
        comment = normalize_comment(payload.get("comment"))
        media_ids = normalize_images(
            payload.get("images") if payload.get("images") is not None else payload.get("review_images")
        )
        target = resolve_ad_target(ad_id, public_only=True)
        eligibility = enforce_review_eligibility(ad_id=ad_id, reviewer=user)
        validated_media = self._validate_media(media_ids, user=user)

        review = frappe.new_doc("AOS Review")
        review.ad = ad_id
        review.reviewer = user
        review.review_key = review_key(reviewer=user, ad_id=ad_id)
        review.rating = rating
        review.title = title
        review.comment = comment
        review.status = STATUS_PENDING
        review.eligibility_basis = eligibility.get("eligibility_basis") or ELIGIBILITY_BASIS_COMMUNICATION
        review.eligibility_reference = eligibility.get("_eligibility_reference")
        review.moderation_generation = 1
        for media in validated_media:
            row = review.append("review_images", {})
            row.media = media.name
            row.image = MediaService().get_public_url(media.name)
        try:
            review.insert(ignore_permissions=True)
            for media in validated_media:
                MediaService().attach_media(
                    media_id=media.name,
                    user=user,
                    purpose="review_image",
                    attached_doctype="AOS Review",
                    attached_name=review.name,
                    attached_field="review_images",
                )
            job = moderation_enqueue(review.name, source="review_create")
        except Exception as exc:
            if is_duplicate_entry_error(exc):
                existing = frappe.db.get_value(
                    "AOS Review",
                    {"reviewer": user, "ad": ad_id},
                    ["name", "status"],
                    as_dict=True,
                )
                state = {
                    "can_review": False,
                    "reason": "REVIEW_ALREADY_EXISTS",
                    "has_reviewed": True,
                    "has_communicated": True,
                    "existing_review_id": existing.name if existing else None,
                    "existing_review_status": existing.status if existing else None,
                    "eligibility_basis": ELIGIBILITY_BASIS_COMMUNICATION,
                }
                raise ReviewConflictError(
                    "You have already reviewed this ad.",
                    code="REVIEW_ALREADY_EXISTS",
                    data={
                        "id": existing.name if existing else None,
                        "status": existing.status if existing else None,
                        "review_viewer_state": public_eligibility_state(state),
                    },
                )
            raise
        review_log("review.created", review_id=review.name, operation="create", status=review.status)
        review_log("review.moderation.dispatched", review_id=review.name, operation="create", status=review.status)
        serialized = self._serialize_one(review.name, viewer=user, include_private=True)
        return {
            # Existing v1 clients consume these flat fields. Keep them while
            # also returning the richer canonical review object.
            "id": review.name,
            "status": review.status,
            "images": serialized.get("image_items", []),
            "review": serialized,
            "moderation_job_id": getattr(job, "name", None),
            "moderation_job_status": getattr(job, "status", None),
            "review_viewer_state": public_eligibility_state(
                {
                    "can_review": False,
                    "reason": "REVIEW_ALREADY_EXISTS",
                    "has_reviewed": True,
                    "has_communicated": True,
                    "existing_review_id": review.name,
                    "existing_review_status": review.status,
                    "eligibility_basis": review.eligibility_basis,
                }
            ),
            "target": {"ad_id": target["ad"].name, "seller_id": public_seller_id_for_name(target["seller"].name)},
        }

    def update(
        self,
        *,
        user: str,
        payload: dict[str, Any],
        moderation_enqueue: Callable[..., Any] = enqueue_review_moderation,
    ) -> dict[str, Any]:
        ensure_known_fields(payload, self.UPDATE_FIELDS)
        review_id = normalize_identifier(payload.get("review_id") or payload.get("review"), field="review")
        review = self._owned_review(review_id, user=user, lock=True)
        if review.status in {STATUS_WITHDRAWN, STATUS_HIDDEN}:
            raise ReviewStateError("Review cannot be updated.", code="REVIEW_UPDATE_NOT_ALLOWED")
        changed = False
        if "rating" in payload:
            value = normalize_rating(payload.get("rating"))
            if int(float(review.rating or 0)) != value:
                review.rating = value
                changed = True
        if "title" in payload:
            value = normalize_title(payload.get("title"))
            if str(review.title or "") != value:
                review.title = value
                changed = True
        if "comment" in payload:
            value = normalize_comment(payload.get("comment"))
            if str(review.comment or "") != value:
                review.comment = value
                changed = True

        media_key_present = "images" in payload or "review_images" in payload
        added: list[str] = []
        removed: list[str] = []
        if media_key_present:
            requested = normalize_images(
                payload.get("images") if "images" in payload else payload.get("review_images")
            )
            existing = [str(row.media or "") for row in review.review_images if row.media]
            added = [media_id for media_id in requested if media_id not in existing]
            removed = [media_id for media_id in existing if media_id not in requested]
            self._validate_media(added, user=user)
            if requested != existing:
                review.set("review_images", [])
                media_service = MediaService()
                for media_id in requested:
                    row = review.append("review_images", {})
                    row.media = media_id
                    row.image = media_service.get_public_url(media_id)
                changed = True

        if not changed:
            return {"review": self._serialize_one(review.name, viewer=user, include_private=True), "idempotent": True}

        review.status = STATUS_PENDING
        review.review_notes = ""
        review.edit_count = int(review.edit_count or 0) + 1
        review.edited_on = now_datetime()
        review.moderation_generation = max(1, int(review.moderation_generation or 0) + 1)
        review.save(ignore_permissions=True)
        media_service = MediaService()
        for media_id in added:
            media_service.attach_media(
                media_id=media_id,
                user=user,
                purpose="review_image",
                attached_doctype="AOS Review",
                attached_name=review.name,
                attached_field="review_images",
            )
        for media_id in removed:
            media_service.release_media(
                media_id=media_id,
                user=user,
                attached_doctype="AOS Review",
                attached_name=review.name,
            )
        job = moderation_enqueue(review.name, source="review_update")
        review_log("review.updated", review_id=review.name, operation="update", status=review.status)
        review_log("review.moderation.dispatched", review_id=review.name, operation="update", status=review.status)
        return {
            "review": self._serialize_one(review.name, viewer=user, include_private=True),
            "moderation_job_id": getattr(job, "name", None),
            "idempotent": False,
        }

    def withdraw(self, *, user: str, review_id: Any) -> dict[str, Any]:
        normalized = normalize_identifier(review_id, field="review")
        review = self._owned_review(normalized, user=user, lock=True)
        if review.status == STATUS_WITHDRAWN:
            return {"id": review.name, "status": review.status, "idempotent": True}
        media_ids = [str(row.media or "") for row in review.review_images if row.media]
        review.status = STATUS_WITHDRAWN
        review.withdrawn_on = now_datetime()
        review.review_notes = ""
        review.set("review_images", [])
        review.save(ignore_permissions=True)
        for media_id in media_ids:
            MediaService().release_media(
                media_id=media_id,
                user=user,
                attached_doctype="AOS Review",
                attached_name=review.name,
            )
        review_log("review.withdrawn", review_id=review.name, operation="delete", status=review.status)
        return {"id": review.name, "status": review.status, "idempotent": False}

    def get(self, *, review_id: Any, viewer: str | None) -> dict[str, Any]:
        normalized = normalize_identifier(review_id, field="review")
        row = frappe.db.get_value(
            "AOS Review",
            normalized,
            self._review_fields(),
            as_dict=True,
        )
        if not row:
            raise ReviewNotFoundError("Review not found.")
        is_owner = bool(viewer and row.reviewer == viewer)
        if row.status != STATUS_APPROVED and not is_owner:
            raise ReviewNotFoundError("Review not found.")
        if row.status in {STATUS_HIDDEN, STATUS_WITHDRAWN} and not is_owner:
            raise ReviewNotFoundError("Review not found.")
        return self._serialize_rows([row], viewer=viewer, include_private=is_owner)[0]

    def list_public(self, *, payload: dict[str, Any], viewer: str | None) -> dict[str, Any]:
        ensure_known_fields(payload, self.LIST_FIELDS)
        ad_id = normalize_identifier(payload.get("ad_id") or payload.get("ad"), field="ad")
        sort = str(payload.get("sort") or "newest").strip()
        if sort not in PUBLIC_SORTS:
            raise ReviewValidationError("Invalid review sort.", code="INVALID_REVIEW_SORT")
        rating = normalize_rating_filter(payload.get("rating"))
        limit, offset = normalize_pagination(payload)
        target = resolve_ad_target(ad_id, public_only=True)
        filters: dict[str, Any] = {"ad": ad_id, "status": STATUS_APPROVED}
        if rating is not None:
            filters["rating"] = rating
        rows = frappe.get_all(
            "AOS Review",
            filters=filters,
            fields=self._review_fields(),
            order_by=PUBLIC_SORTS[sort],
            limit=limit,
            start=offset,
        )
        total = frappe.db.count("AOS Review", filters)
        distribution = rating_distribution(ad_id)
        return {
            "summary": {
                "average_rating": float(target["ad"].average_rating or 0),
                "total_reviews": int(target["ad"].total_reviews or 0),
                "total_reviews_display": humanize_count(target["ad"].total_reviews or 0),
                "distribution": distribution,
                "distribution_display": {key: humanize_count(value) for key, value in distribution.items()},
            },
            "reviews": self._serialize_rows(rows, viewer=viewer, include_private=False),
            "pagination": self._pagination(total=total, limit=limit, offset=offset),
        }

    def list_my(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, self.PRIVATE_LIST_FIELDS)
        sort = str(payload.get("sort") or "newest").strip()
        if sort not in SELF_SORTS:
            raise ReviewValidationError("Invalid review sort.", code="INVALID_REVIEW_SORT")
        rating = normalize_rating_filter(payload.get("rating"))
        limit, offset = normalize_pagination(payload)
        filters: dict[str, Any] = {"reviewer": user}
        requested_status = str(payload.get("status") or "").strip()
        if requested_status:
            if requested_status not in {STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_HIDDEN, STATUS_WITHDRAWN}:
                raise ReviewValidationError("Invalid review status.", code="INVALID_REVIEW_STATUS")
            filters["status"] = requested_status
        if rating is not None:
            filters["rating"] = rating
        rows = frappe.get_all(
            "AOS Review",
            filters=filters,
            fields=self._review_fields(),
            order_by=SELF_SORTS[sort],
            limit=limit,
            start=offset,
        )
        total = frappe.db.count("AOS Review", filters)
        return {
            "reviews": self._serialize_rows(rows, viewer=user, include_private=True),
            "pagination": self._pagination(total=total, limit=limit, offset=offset),
        }

    def list_received(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, self.PRIVATE_LIST_FIELDS)
        seller = frappe.db.get_value("AOS Seller", {"user": user}, ["name", "status"], as_dict=True)
        if not seller:
            raise ReviewPermissionError("Seller account required.", code="SELLER_REQUIRED")
        sort = str(payload.get("sort") or "newest").strip()
        if sort not in PUBLIC_SORTS:
            raise ReviewValidationError("Invalid review sort.", code="INVALID_REVIEW_SORT")
        rating = normalize_rating_filter(payload.get("rating"))
        limit, offset = normalize_pagination(payload)
        params: dict[str, Any] = {"seller": seller.name, "status": STATUS_APPROVED, "limit": limit, "offset": offset}
        rating_sql = ""
        if rating is not None:
            rating_sql = " AND r.rating = %(rating)s"
            params["rating"] = rating
        order_by = PUBLIC_SORTS[sort].replace("creation", "r.creation").replace("name", "r.name").replace("rating", "r.rating").replace("like_count", "r.like_count")
        rows = frappe.db.sql(
            f"""
            SELECT r.name, r.ad, r.rating, r.title, r.comment, r.reviewer, r.status,
                   r.creation, r.modified, r.like_count, r.dislike_count,
                   r.eligibility_basis, r.edit_count, r.edited_on, r.review_notes,
                   r.withdrawn_on, r.moderation_generation
            FROM `tabAOS Review` r
            INNER JOIN `tabAOS Ad` a ON a.name = r.ad
            WHERE a.seller = %(seller)s AND r.status = %(status)s {rating_sql}
            ORDER BY {order_by}
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
            as_dict=True,
        )
        count_row = frappe.db.sql(
            f"""
            SELECT COUNT(r.name) AS total
            FROM `tabAOS Review` r
            INNER JOIN `tabAOS Ad` a ON a.name = r.ad
            WHERE a.seller = %(seller)s AND r.status = %(status)s {rating_sql}
            """,
            params,
            as_dict=True,
        )[0]
        total = int(count_row.total or 0)
        return {
            "seller": {"id": public_seller_id_for_name(seller.name), "status": seller.status},
            "reviews": self._serialize_rows(rows, viewer=user, include_private=False),
            "pagination": self._pagination(total=total, limit=limit, offset=offset),
        }

    def toggle_reaction(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, self.REACTION_FIELDS)
        review_id = normalize_identifier(payload.get("review_id") or payload.get("review"), field="review")
        reaction = normalize_reaction(payload.get("reaction"))
        review = frappe.db.get_value(
            "AOS Review", review_id, ["name", "reviewer", "status", "ad"], as_dict=True
        )
        if not review or review.status != STATUS_APPROVED:
            raise ReviewNotFoundError("Review not found.")
        if review.reviewer == user:
            raise ReviewPermissionError("You cannot react to your own review.", code="REVIEW_SELF_VOTE_NOT_ALLOWED")
        seller_user = frappe.db.get_value(
            "AOS Seller",
            frappe.db.get_value("AOS Ad", review.ad, "seller"),
            "user",
        )
        if seller_user and is_blocked_between(user, seller_user):
            raise ReviewPermissionError("This interaction is blocked.", code="USER_BLOCKED")
        rows = frappe.db.sql(
            """
            SELECT name, reaction FROM `tabAOS Review Reaction`
            WHERE review = %s AND user = %s
            LIMIT 1 FOR UPDATE
            """,
            (review_id, user),
            as_dict=True,
        )
        status = "added"
        active_reaction: str | None = reaction
        if not rows:
            doc = frappe.new_doc(REACTION_DOCTYPE)
            doc.review = review_id
            doc.user = user
            doc.reaction = reaction
            try:
                doc.insert(ignore_permissions=True)
            except Exception as exc:
                if not is_duplicate_entry_error(exc):
                    raise
                existing = frappe.db.get_value(
                    REACTION_DOCTYPE,
                    {"review": review_id, "user": user},
                    ["name", "reaction"],
                    as_dict=True,
                )
                if not existing:
                    raise ReviewConflictError(
                        "Review reaction could not be reconciled.",
                        code="REVIEW_REACTION_CONFLICT",
                    )
                if existing.reaction == reaction:
                    status = "added"
                else:
                    status = "switched"
                    frappe.db.set_value(REACTION_DOCTYPE, existing.name, "reaction", reaction)
        elif rows[0].reaction == reaction:
            frappe.delete_doc(REACTION_DOCTYPE, rows[0].name, ignore_permissions=True, force=True)
            status = "removed"
            active_reaction = None
        else:
            frappe.db.set_value(REACTION_DOCTYPE, rows[0].name, "reaction", reaction)
            status = "switched"
        recompute_review_reaction_counts(review_id=review_id, lock_review=True)
        counts = frappe.db.get_value(
            "AOS Review", review_id, ["like_count", "dislike_count"], as_dict=True
        )
        review_log("review.helpful.changed", review_id=review_id, operation=status, status=active_reaction)
        return {
            "status": status,
            "reaction": active_reaction,
            "like_count": int(counts.like_count or 0),
            "dislike_count": int(counts.dislike_count or 0),
        }

    def report(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, self.REPORT_FIELDS)
        review_id = normalize_identifier(payload.get("review_id") or payload.get("review"), field="review")
        reason = normalize_report_reason(payload.get("reason"))
        details = normalize_report_details(payload.get("details"))
        if not frappe.db.exists("AOS Report Reason", {"name": reason, "is_active": 1}):
            raise ReviewValidationError(
                "Invalid report reason.",
                code="INVALID_REVIEW_REPORT_REASON",
            )
        review = frappe.db.get_value("AOS Review", review_id, ["name", "reviewer", "status"], as_dict=True)
        if not review or review.status != STATUS_APPROVED:
            raise ReviewNotFoundError("Review not found.")
        if review.reviewer == user:
            raise ReviewPermissionError("You cannot report your own review.", code="REVIEW_REPORT_NOT_ALLOWED")
        existing = frappe.db.get_value(
            REPORT_DOCTYPE,
            {"review": review_id, "reported_by": user},
            ["name", "status"],
            as_dict=True,
        )
        if existing:
            return {"id": existing.name, "status": existing.status, "idempotent": True}
        doc = frappe.new_doc(REPORT_DOCTYPE)
        doc.review = review_id
        doc.reported_by = user
        doc.reason = reason
        doc.details = details
        doc.status = "Reviewing"
        try:
            doc.insert(ignore_permissions=True)
        except Exception as exc:
            if not is_duplicate_entry_error(exc):
                raise
            existing = frappe.db.get_value(
                REPORT_DOCTYPE,
                {"review": review_id, "reported_by": user},
                ["name", "status"],
                as_dict=True,
            )
            if not existing:
                raise ReviewConflictError(
                    "Review report could not be reconciled.",
                    code="REVIEW_REPORT_CONFLICT",
                )
            return {"id": existing.name, "status": existing.status, "idempotent": True}
        review_log("review.reported", review_id=review_id, operation=reason, status=doc.status)
        return {"id": doc.name, "status": doc.status, "idempotent": False}

    @staticmethod
    def _validate_media(media_ids: list[str], *, user: str) -> list[Any]:
        service = MediaService()
        return [
            service.validate_media_for_use(media_id=media_id, user=user, purpose="review_image")
            for media_id in media_ids
        ]

    @staticmethod
    def _owned_review(review_id: str, *, user: str, lock: bool) -> Any:
        if lock:
            rows = frappe.db.sql(
                "SELECT name, reviewer FROM `tabAOS Review` WHERE name = %s FOR UPDATE",
                (review_id,),
                as_dict=True,
            )
            if not rows:
                raise ReviewNotFoundError("Review not found.")
            if rows[0].reviewer != user:
                raise ReviewPermissionError(
                    "Review update is not allowed.",
                    code="REVIEW_UPDATE_NOT_ALLOWED",
                )
        elif not frappe.db.exists("AOS Review", review_id):
            raise ReviewNotFoundError("Review not found.")
        review = frappe.get_doc("AOS Review", review_id)
        if review.reviewer != user:
            raise ReviewPermissionError(
                "Review update is not allowed.",
                code="REVIEW_UPDATE_NOT_ALLOWED",
            )
        return review

    @staticmethod
    def _review_fields() -> list[str]:
        return [
            "name", "ad", "rating", "title", "comment", "reviewer", "status",
            "creation", "modified", "like_count", "dislike_count", "eligibility_basis",
            "edit_count", "edited_on", "review_notes", "withdrawn_on", "moderation_generation",
        ]

    def _serialize_one(self, review_id: str, *, viewer: str | None, include_private: bool) -> dict[str, Any]:
        row = frappe.db.get_value("AOS Review", review_id, self._review_fields(), as_dict=True)
        return self._serialize_rows([row], viewer=viewer, include_private=include_private)[0]

    @staticmethod
    def _serialize_rows(rows: list[Any], *, viewer: str | None, include_private: bool) -> list[dict[str, Any]]:
        return serialize_reviews(rows, viewer=viewer, include_private=include_private)

    @staticmethod
    def _sync_reaction_counts(review_id: str) -> None:
        """Backward-compatible internal delegate to the canonical aggregate helper."""
        recompute_review_reaction_counts(review_id=review_id)

    @staticmethod
    def _pagination(*, total: int, limit: int, offset: int) -> dict[str, Any]:
        return {
            "total": max(0, int(total)),
            "limit": int(limit),
            "offset": int(offset),
            "has_more": int(offset) + int(limit) < int(total),
        }
