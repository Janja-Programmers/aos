"""Production Reviews application service.

Reviews owns review content, lifecycle, reaction relationships, and review-derived
aggregates. Authentication, Ad visibility, Seller identity, Media, Notifications,
and moderation infrastructure remain authoritative in their hardened features.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.api.shared.blocking import is_blocked_between
from aos.api.shared.db import is_duplicate_entry_error
from aos.services.ads.errors import AdsNotFoundError
from aos.services.ads.visibility import require_public_ad_for_viewer
from aos.services.media.media_service import MediaService
from aos.services.moderation_service import enqueue_review_moderation
from aos.services.sellers.identity import public_seller_id_for_name

from .aggregates import rating_distribution
from .constants import (
    ALL_STATUSES,
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
from .eligibility import enforce_review_eligibility, get_review_eligibility, public_eligibility_state, resolve_ad_target, review_key
from .errors import ReviewConflictError, ReviewNotFoundError, ReviewPermissionError, ReviewStateError, ReviewValidationError
from .ids import resolve_review_name, review_public_id
from .observability import review_log
from .pagination import decode_cursor, encode_cursor, query_fingerprint
from .serializers import serialize_reviews
from .validation import (
    ensure_known_fields,
    normalize_comment,
    normalize_flag,
    normalize_identifier,
    normalize_images,
    normalize_limit,
    normalize_rating,
    normalize_rating_filter,
    normalize_report_details,
    normalize_report_reason,
    normalize_title,
    normalize_version,
)

_REVIEW_FIELDS = [
    "name", "public_id", "ad", "rating", "title", "comment", "reviewer", "status",
    "creation", "modified", "like_count", "dislike_count", "eligibility_basis",
    "edit_count", "edited_on", "withdrawn_on", "moderation_generation",
]

_SORT_SPECS: dict[str, tuple[tuple[str, str], ...]] = {
    "newest": (("creation", "DESC"), ("public_id", "DESC")),
    "oldest": (("creation", "ASC"), ("public_id", "ASC")),
    "helpful": (("like_count", "DESC"), ("creation", "DESC"), ("public_id", "DESC")),
    "rating_high": (("rating", "DESC"), ("creation", "DESC"), ("public_id", "DESC")),
    "rating_low": (("rating", "ASC"), ("creation", "DESC"), ("public_id", "DESC")),
}


class ReviewService:
    """Review use-cases; the HTTP request owns commit/rollback semantics."""

    CREATE_FIELDS = frozenset({"ad_id", "rating", "title", "comment", "media"})
    UPDATE_FIELDS = frozenset({"review_id", "version", "rating", "title", "comment", "media"})
    ID_FIELDS = frozenset({"review_id", "version"})
    GET_FIELDS = frozenset({"review_id"})
    VIEWER_STATE_FIELDS = frozenset({"ad_id"})
    LIST_FIELDS = frozenset({"ad_id", "sort", "rating", "with_media", "limit", "cursor"})
    PRIVATE_LIST_FIELDS = frozenset({"sort", "status", "rating", "with_media", "limit", "cursor"})
    REACTION_FIELDS = frozenset({"review_id"})
    REPORT_FIELDS = frozenset({"review_id", "reason", "details"})

    def viewer_state(self, *, payload: dict[str, Any], viewer: str | None) -> dict[str, Any]:
        ensure_known_fields(payload, self.VIEWER_STATE_FIELDS)
        ad_id = normalize_identifier(payload.get("ad_id"), field="ad_id")
        state = get_review_eligibility(ad_id=ad_id, reviewer=viewer)
        review_log(
            "review.eligibility.checked",
            review_id=state.get("existing_review_id") or ad_id,
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
        ad_id = normalize_identifier(payload.get("ad_id"), field="ad_id")
        rating = normalize_rating(payload.get("rating"))
        title = normalize_title(payload.get("title"))
        comment = normalize_comment(payload.get("comment"))
        media_ids = normalize_images(payload.get("media"))

        eligibility = enforce_review_eligibility(ad_id=ad_id, reviewer=user)
        internal_ad_name = str(eligibility.get("_ad_name") or "")
        if not internal_ad_name:
            target = resolve_ad_target(ad_id, viewer=user)
            internal_ad_name = str(target["ad"].name)
        validated_media = self._validate_media(media_ids, user=user)

        review = frappe.new_doc("AOS Review")
        review.ad = internal_ad_name
        review.reviewer = user
        review.review_key = review_key(reviewer=user, ad_id=internal_ad_name)
        review.rating = rating
        review.title = title
        review.comment = comment
        review.status = STATUS_PENDING
        review.eligibility_basis = eligibility.get("eligibility_basis") or ELIGIBILITY_BASIS_COMMUNICATION
        review.eligibility_reference = eligibility.get("_eligibility_reference")
        review.moderation_generation = 1
        for media in validated_media:
            review.append("review_images", {"media": media.name})

        try:
            review.insert(ignore_permissions=True)
        except Exception as exc:
            if not is_duplicate_entry_error(exc):
                raise
            existing = frappe.db.get_value(
                "AOS Review",
                {"reviewer": user, "ad": internal_ad_name},
                ["public_id", "status"],
                as_dict=True,
            )
            if not existing:
                raise
            raise ReviewConflictError(
                "You have already reviewed this ad.",
                code="REVIEW_ALREADY_EXISTS",
                data={"review_id": existing.public_id, "status": existing.status},
            ) from None

        media_service = MediaService()
        for media in validated_media:
            media_service.attach_media(
                media_id=media.name,
                user=user,
                purpose="review_image",
                attached_doctype="AOS Review",
                attached_name=review.name,
                attached_field="review_images",
            )
        job = moderation_enqueue(review.name, source="review_create")
        review_log("review.created", review_id=review.public_id, operation="create", status=review.status)
        return {
            "review": self._serialize_one(review.name, viewer=user, include_private=True),
            "moderation": {"queued": bool(job)},
            "review_viewer_state": public_eligibility_state(
                {
                    "can_review": False,
                    "reason": "REVIEW_ALREADY_EXISTS",
                    "has_reviewed": True,
                    "has_communicated": True,
                    "existing_review_id": review.public_id,
                    "existing_review_status": review.status,
                    "eligibility_basis": review.eligibility_basis,
                }
            ),
        }

    def update(
        self,
        *,
        user: str,
        payload: dict[str, Any],
        moderation_enqueue: Callable[..., Any] = enqueue_review_moderation,
    ) -> dict[str, Any]:
        ensure_known_fields(payload, self.UPDATE_FIELDS)
        public_id = normalize_identifier(payload.get("review_id"), field="review_id")
        version = normalize_version(payload.get("version"))
        review = self._owned_review(public_id, user=user, lock=True)
        if review.status in {STATUS_WITHDRAWN, STATUS_HIDDEN}:
            raise ReviewStateError("Review cannot be updated.", code="REVIEW_UPDATE_NOT_ALLOWED")
        self._assert_version(review, version)

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

        media_service = MediaService()
        added: list[str] = []
        removed: list[str] = []
        if "media" in payload:
            requested = normalize_images(payload.get("media"))
            existing = [str(row.media or "") for row in review.review_images if row.media]
            added = [media_id for media_id in requested if media_id not in existing]
            removed = [media_id for media_id in existing if media_id not in requested]
            self._validate_media(added, user=user)
            if requested != existing:
                review.set("review_images", [])
                for media_id in requested:
                    review.append("review_images", {"media": media_id})
                changed = True

        if not changed:
            return {"review": self._serialize_one(review.name, viewer=user, include_private=True), "changed": False}

        review.flags.aos_review_action = "owner_edit"
        review.status = STATUS_PENDING
        review.review_notes = ""
        review.edit_count = int(review.edit_count or 0) + 1
        review.edited_on = now_datetime()
        review.moderation_generation = max(1, int(review.moderation_generation or 0) + 1)
        review.save(ignore_permissions=True)

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
        review_log("review.updated", review_id=review.public_id, operation="update", status=review.status)
        return {
            "review": self._serialize_one(review.name, viewer=user, include_private=True),
            "changed": True,
            "moderation": {"queued": bool(job)},
        }

    def withdraw(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, self.ID_FIELDS)
        public_id = normalize_identifier(payload.get("review_id"), field="review_id")
        version = normalize_version(payload.get("version"))
        review = self._owned_review(public_id, user=user, lock=True)
        if review.status == STATUS_WITHDRAWN:
            return {"review_id": review.public_id, "status": review.status, "changed": False}
        self._assert_version(review, version)

        media_ids = [str(row.media or "") for row in review.review_images if row.media]
        review.flags.aos_review_action = "owner_withdraw"
        review.status = STATUS_WITHDRAWN
        review.withdrawn_on = now_datetime()
        review.review_notes = ""
        review.set("review_images", [])
        review.save(ignore_permissions=True)
        media_service = MediaService()
        for media_id in media_ids:
            media_service.release_media(
                media_id=media_id,
                user=user,
                attached_doctype="AOS Review",
                attached_name=review.name,
            )
        review_log("review.withdrawn", review_id=review.public_id, operation="delete", status=review.status)
        return {"review_id": review.public_id, "status": review.status, "changed": True}

    def get(self, *, payload: dict[str, Any], viewer: str | None) -> dict[str, Any]:
        ensure_known_fields(payload, self.GET_FIELDS)
        public_id = normalize_identifier(payload.get("review_id"), field="review_id")
        row = frappe.db.get_value("AOS Review", {"public_id": public_id}, _REVIEW_FIELDS, as_dict=True)
        if not row:
            raise ReviewNotFoundError("Review not found.")
        is_owner = bool(viewer and viewer != "Guest" and row.reviewer == viewer)
        if not is_owner:
            if row.status != STATUS_APPROVED:
                raise ReviewNotFoundError("Review not found.")
            ad_public_id = frappe.db.get_value("AOS Ad", row.ad, "public_id")
            try:
                require_public_ad_for_viewer(public_id=ad_public_id, viewer=viewer or "Guest")
            except AdsNotFoundError as exc:
                raise ReviewNotFoundError("Review not found.") from exc
            if viewer and viewer != "Guest" and is_blocked_between(viewer, row.reviewer):
                raise ReviewNotFoundError("Review not found.")
        return self._serialize_rows([row], viewer=viewer, include_private=is_owner)[0]

    def list_public(self, *, payload: dict[str, Any], viewer: str | None) -> dict[str, Any]:
        ensure_known_fields(payload, self.LIST_FIELDS)
        ad_id = normalize_identifier(payload.get("ad_id"), field="ad_id")
        sort = self._normalize_sort(payload.get("sort"), allowed=PUBLIC_SORTS)
        rating = normalize_rating_filter(payload.get("rating"))
        with_media = normalize_flag(payload.get("with_media"), field="with_media")
        limit = normalize_limit(payload.get("limit"))
        target = resolve_ad_target(ad_id, viewer=viewer or "Guest")

        query_context = {"ad_id": ad_id, "rating": rating, "with_media": with_media}
        query_key = query_fingerprint(query_context)
        cursor = decode_cursor(
            payload.get("cursor"),
            scope="public",
            sort=sort,
            query_key=query_key,
            expected_keys=len(_SORT_SPECS[sort]),
        )
        params: dict[str, Any] = {
            "ad": target["ad"].name,
            "status": STATUS_APPROVED,
            "limit": limit + 1,
        }
        where = ["r.ad = %(ad)s", "r.status = %(status)s"]
        if rating is not None:
            where.append("r.rating = %(rating)s")
            params["rating"] = rating
        self._append_media_filter(where, with_media)
        self._append_block_filter(where, params, viewer=viewer)
        if cursor:
            where.append(self._cursor_clause(sort=sort, keys=cursor, params=params, prefix="r"))

        rows = frappe.db.sql(
            f"""
            SELECT {self._sql_review_fields('r')}
            FROM `tabAOS Review` r
            WHERE {' AND '.join(where)}
            ORDER BY {self._order_by(sort, prefix='r')}
            LIMIT %(limit)s
            """,
            params,
            as_dict=True,
        )
        page, has_more, next_cursor = self._page(rows, limit=limit, scope="public", sort=sort, query_key=query_key)
        distribution = rating_distribution(str(target["ad"].name))
        return {
            "summary": {
                "average_rating": float(target["ad"].average_rating or 0),
                "total_reviews": int(target["ad"].total_reviews or 0),
                "distribution": distribution,
            },
            "reviews": self._serialize_rows(page, viewer=viewer, include_private=False),
            "pagination": {"limit": limit, "has_more": has_more, "next_cursor": next_cursor},
        }

    def list_my(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, self.PRIVATE_LIST_FIELDS)
        sort = self._normalize_sort(payload.get("sort"), allowed=SELF_SORTS)
        rating = normalize_rating_filter(payload.get("rating"))
        with_media = normalize_flag(payload.get("with_media"), field="with_media")
        limit = normalize_limit(payload.get("limit"))
        requested_status = str(payload.get("status") or "").strip()
        if requested_status and requested_status not in ALL_STATUSES:
            raise ReviewValidationError("Invalid review status.", code="INVALID_REVIEW_STATUS")
        query_context = {"status": requested_status or None, "rating": rating, "with_media": with_media}
        query_key = query_fingerprint(query_context)
        cursor = decode_cursor(
            payload.get("cursor"),
            scope="my",
            sort=sort,
            query_key=query_key,
            expected_keys=len(_SORT_SPECS[sort]),
        )
        params: dict[str, Any] = {"reviewer": user, "limit": limit + 1}
        where = ["r.reviewer = %(reviewer)s"]
        if requested_status:
            where.append("r.status = %(status)s")
            params["status"] = requested_status
        if rating is not None:
            where.append("r.rating = %(rating)s")
            params["rating"] = rating
        self._append_media_filter(where, with_media)
        if cursor:
            where.append(self._cursor_clause(sort=sort, keys=cursor, params=params, prefix="r"))
        rows = frappe.db.sql(
            f"""
            SELECT {self._sql_review_fields('r')}
            FROM `tabAOS Review` r
            WHERE {' AND '.join(where)}
            ORDER BY {self._order_by(sort, prefix='r')}
            LIMIT %(limit)s
            """,
            params,
            as_dict=True,
        )
        page, has_more, next_cursor = self._page(rows, limit=limit, scope="my", sort=sort, query_key=query_key)
        return {
            "reviews": self._serialize_rows(page, viewer=user, include_private=True),
            "pagination": {"limit": limit, "has_more": has_more, "next_cursor": next_cursor},
        }

    def list_received(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, self.PRIVATE_LIST_FIELDS)
        seller = frappe.db.get_value("AOS Seller", {"user": user}, ["name", "status"], as_dict=True)
        if not seller:
            raise ReviewPermissionError("Seller account required.", code="SELLER_REQUIRED")
        sort = self._normalize_sort(payload.get("sort"), allowed=PUBLIC_SORTS)
        rating = normalize_rating_filter(payload.get("rating"))
        with_media = normalize_flag(payload.get("with_media"), field="with_media")
        limit = normalize_limit(payload.get("limit"))
        requested_status = str(payload.get("status") or "").strip()
        if requested_status and requested_status != STATUS_APPROVED:
            raise ReviewValidationError("Invalid review status.", code="INVALID_REVIEW_STATUS")
        query_context = {"rating": rating, "with_media": with_media}
        query_key = query_fingerprint(query_context)
        cursor = decode_cursor(
            payload.get("cursor"),
            scope="received",
            sort=sort,
            query_key=query_key,
            expected_keys=len(_SORT_SPECS[sort]),
        )
        params: dict[str, Any] = {"seller": seller.name, "status": STATUS_APPROVED, "limit": limit + 1}
        where = ["a.seller = %(seller)s", "r.status = %(status)s"]
        if rating is not None:
            where.append("r.rating = %(rating)s")
            params["rating"] = rating
        self._append_media_filter(where, with_media)
        if cursor:
            where.append(self._cursor_clause(sort=sort, keys=cursor, params=params, prefix="r"))
        rows = frappe.db.sql(
            f"""
            SELECT {self._sql_review_fields('r')}
            FROM `tabAOS Review` r
            INNER JOIN `tabAOS Ad` a ON a.name = r.ad
            WHERE {' AND '.join(where)}
            ORDER BY {self._order_by(sort, prefix='r')}
            LIMIT %(limit)s
            """,
            params,
            as_dict=True,
        )
        page, has_more, next_cursor = self._page(rows, limit=limit, scope="received", sort=sort, query_key=query_key)
        return {
            "seller": {"id": public_seller_id_for_name(seller.name), "status": seller.status},
            "reviews": self._serialize_rows(page, viewer=user, include_private=False),
            "pagination": {"limit": limit, "has_more": has_more, "next_cursor": next_cursor},
        }

    def like(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._set_reaction(user=user, payload=payload, target="Like", remove_only=False)

    def unlike(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._set_reaction(user=user, payload=payload, target="Like", remove_only=True)

    def dislike(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._set_reaction(user=user, payload=payload, target="Dislike", remove_only=False)

    def undislike(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._set_reaction(user=user, payload=payload, target="Dislike", remove_only=True)

    def report(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, self.REPORT_FIELDS)
        public_id = normalize_identifier(payload.get("review_id"), field="review_id")
        reason = normalize_report_reason(payload.get("reason"))
        details = normalize_report_details(payload.get("details"))
        if not frappe.db.exists("AOS Report Reason", {"name": reason, "is_active": 1}):
            raise ReviewValidationError("Invalid report reason.", code="INVALID_REVIEW_REPORT_REASON")

        review = self._public_review_row(public_id=public_id, viewer=user, lock=True)
        if review.reviewer == user:
            raise ReviewPermissionError("You cannot report your own review.", code="REVIEW_REPORT_NOT_ALLOWED")
        existing = frappe.db.get_value(
            REPORT_DOCTYPE,
            {"review": review.name, "reported_by": user},
            ["name", "status"],
            as_dict=True,
        )
        if existing:
            return {"report_id": existing.name, "status": existing.status, "changed": False}
        doc = frappe.new_doc(REPORT_DOCTYPE)
        doc.review = review.name
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
                {"review": review.name, "reported_by": user},
                ["name", "status"],
                as_dict=True,
            )
            if not existing:
                raise ReviewConflictError("Review report could not be reconciled.", code="REVIEW_REPORT_CONFLICT") from None
            return {"report_id": existing.name, "status": existing.status, "changed": False}
        review_log("review.reported", review_id=public_id, operation=reason, status=doc.status)
        return {"report_id": doc.name, "status": doc.status, "changed": True}

    def _set_reaction(self, *, user: str, payload: dict[str, Any], target: str, remove_only: bool) -> dict[str, Any]:
        ensure_known_fields(payload, self.REACTION_FIELDS)
        public_id = normalize_identifier(payload.get("review_id"), field="review_id")
        review = self._public_review_row(public_id=public_id, viewer=user, lock=False)
        if review.reviewer == user:
            raise ReviewPermissionError("You cannot react to your own review.", code="REVIEW_SELF_VOTE_NOT_ALLOWED")
        if is_blocked_between(user, review.reviewer):
            raise ReviewNotFoundError("Review not found.")

        rows = frappe.db.sql(
            """
            SELECT name, reaction
            FROM `tabAOS Review Reaction`
            WHERE review = %s AND user = %s
            LIMIT 1 FOR UPDATE
            """,
            (review.name, user),
            as_dict=True,
        )
        changed = False
        current = str(rows[0].reaction) if rows else None
        if remove_only:
            if rows and current == target:
                frappe.delete_doc(REACTION_DOCTYPE, rows[0].name, ignore_permissions=True, force=True)
                current = None
                changed = True
        elif not rows:
            doc = frappe.new_doc(REACTION_DOCTYPE)
            doc.review = review.name
            doc.user = user
            doc.reaction = target
            try:
                doc.insert(ignore_permissions=True)
                current = target
                changed = True
            except Exception as exc:
                if not is_duplicate_entry_error(exc):
                    raise
                rows = frappe.db.sql(
                    """
                    SELECT name, reaction FROM `tabAOS Review Reaction`
                    WHERE review = %s AND user = %s LIMIT 1 FOR UPDATE
                    """,
                    (review.name, user),
                    as_dict=True,
                )
                if not rows:
                    raise ReviewConflictError("Review reaction could not be reconciled.", code="REVIEW_REACTION_CONFLICT") from None
                current = str(rows[0].reaction)
                if current != target:
                    existing = frappe.get_doc(REACTION_DOCTYPE, rows[0].name)
                    existing.reaction = target
                    existing.save(ignore_permissions=True)
                    current = target
                    changed = True
        elif current != target:
            existing = frappe.get_doc(REACTION_DOCTYPE, rows[0].name)
            existing.reaction = target
            existing.save(ignore_permissions=True)
            current = target
            changed = True

        counts = frappe.db.get_value("AOS Review", review.name, ["like_count", "dislike_count"], as_dict=True)
        review_log("review.reaction.changed", review_id=public_id, operation=("remove" if remove_only else target.lower()), status=current)
        return {
            "review_id": public_id,
            "reaction": current,
            "changed": changed,
            "like_count": max(0, int(counts.like_count or 0)),
            "dislike_count": max(0, int(counts.dislike_count or 0)),
        }

    def _public_review_row(self, *, public_id: str, viewer: str, lock: bool) -> Any:
        lock_sql = " FOR UPDATE" if lock else ""
        rows = frappe.db.sql(
            f"""
            SELECT r.name, r.public_id, r.reviewer, r.status, r.ad, a.public_id AS ad_public_id
            FROM `tabAOS Review` r
            INNER JOIN `tabAOS Ad` a ON a.name = r.ad
            WHERE r.public_id = %s AND r.status = %s
            LIMIT 1{lock_sql}
            """,
            (public_id, STATUS_APPROVED),
            as_dict=True,
        )
        if not rows:
            raise ReviewNotFoundError("Review not found.")
        row = rows[0]
        try:
            require_public_ad_for_viewer(public_id=row.ad_public_id, viewer=viewer)
        except AdsNotFoundError as exc:
            raise ReviewNotFoundError("Review not found.") from exc
        return row

    @staticmethod
    def _validate_media(media_ids: list[str], *, user: str) -> list[Any]:
        media_service = MediaService()
        return [
            media_service.validate_media_for_use(media_id=media_id, user=user, purpose="review_image")
            for media_id in media_ids
        ]

    @staticmethod
    def _assert_version(review: Any, version: str) -> None:
        if str(review.modified or "") != version:
            raise ReviewConflictError(
                "Review changed since it was loaded.",
                code="REVIEW_VERSION_CONFLICT",
                data={"review_id": review.public_id, "version": str(review.modified or "")},
            )

    @staticmethod
    def _owned_review(public_id: str, *, user: str, lock: bool) -> Any:
        name = resolve_review_name(public_id)
        if lock:
            rows = frappe.db.sql(
                "SELECT name, reviewer FROM `tabAOS Review` WHERE name = %s FOR UPDATE",
                (name,),
                as_dict=True,
            )
            if not rows:
                raise ReviewNotFoundError("Review not found.")
            if rows[0].reviewer != user:
                raise ReviewPermissionError("Review update is not allowed.", code="REVIEW_UPDATE_NOT_ALLOWED")
        review = frappe.get_doc("AOS Review", name)
        if review.reviewer != user:
            raise ReviewPermissionError("Review update is not allowed.", code="REVIEW_UPDATE_NOT_ALLOWED")
        return review

    def _serialize_one(self, review_name: str, *, viewer: str | None, include_private: bool) -> dict[str, Any]:
        row = frappe.db.get_value("AOS Review", review_name, _REVIEW_FIELDS, as_dict=True)
        if not row:
            raise ReviewNotFoundError("Review not found.")
        return self._serialize_rows([row], viewer=viewer, include_private=include_private)[0]

    @staticmethod
    def _serialize_rows(rows: list[Any], *, viewer: str | None, include_private: bool) -> list[dict[str, Any]]:
        return serialize_reviews(rows, viewer=viewer, include_private=include_private)

    @staticmethod
    def _normalize_sort(value: Any, *, allowed: frozenset[str]) -> str:
        sort = str(value or "newest").strip().lower()
        if sort not in allowed or sort not in _SORT_SPECS:
            raise ReviewValidationError("Invalid review sort.", code="INVALID_REVIEW_SORT")
        return sort

    @staticmethod
    def _sql_review_fields(prefix: str) -> str:
        return ", ".join(f"{prefix}.{field}" for field in _REVIEW_FIELDS)

    @staticmethod
    def _order_by(sort: str, *, prefix: str) -> str:
        return ", ".join(f"{prefix}.{field} {direction}" for field, direction in _SORT_SPECS[sort])

    @staticmethod
    def _append_media_filter(where: list[str], with_media: bool | None) -> None:
        if with_media is True:
            where.append("EXISTS (SELECT 1 FROM `tabAOS Review Image` ri WHERE ri.parent = r.name AND ri.parenttype = 'AOS Review')")
        elif with_media is False:
            where.append("NOT EXISTS (SELECT 1 FROM `tabAOS Review Image` ri WHERE ri.parent = r.name AND ri.parenttype = 'AOS Review')")

    @staticmethod
    def _append_block_filter(where: list[str], params: dict[str, Any], *, viewer: str | None) -> None:
        if not viewer or viewer == "Guest":
            return
        params["viewer"] = viewer
        where.append(
            """NOT EXISTS (
                SELECT 1 FROM `tabAOS User Block` b
                WHERE b.status = 'Active'
                  AND ((b.blocker_user = %(viewer)s AND b.blocked_user = r.reviewer)
                    OR (b.blocker_user = r.reviewer AND b.blocked_user = %(viewer)s))
            )"""
        )

    @staticmethod
    def _cursor_clause(*, sort: str, keys: list[Any], params: dict[str, Any], prefix: str) -> str:
        spec = _SORT_SPECS[sort]
        parts: list[str] = []
        for index, (field, direction) in enumerate(spec):
            equals = []
            for before in range(index):
                pname = f"cursor_{before}"
                params[pname] = keys[before]
                equals.append(f"{prefix}.{spec[before][0]} = %({pname})s")
            pname = f"cursor_{index}"
            params[pname] = keys[index]
            operator = "<" if direction == "DESC" else ">"
            comparison = f"{prefix}.{field} {operator} %({pname})s"
            parts.append("(" + " AND ".join([*equals, comparison]) + ")")
        return "(" + " OR ".join(parts) + ")"

    @staticmethod
    def _cursor_values(row: Any, sort: str) -> list[Any]:
        values: list[Any] = []
        for field, _direction in _SORT_SPECS[sort]:
            value = getattr(row, field)
            values.append(str(value) if field in {"creation", "public_id"} else int(value or 0))
        return values

    def _page(
        self,
        rows: list[Any],
        *,
        limit: int,
        scope: str,
        sort: str,
        query_key: str,
    ) -> tuple[list[Any], bool, str | None]:
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            next_cursor = encode_cursor(
                scope=scope,
                sort=sort,
                query_key=query_key,
                values=self._cursor_values(page[-1], sort),
            )
        return page, has_more, next_cursor
