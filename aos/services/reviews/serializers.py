"""Canonical privacy-safe Reviews serializers with batched dependencies."""

from __future__ import annotations

from typing import Any, Iterable

import frappe

from aos.api.shared.user_display import get_user_display_map
from aos.services.media.media_service import MediaService

from .constants import ELIGIBILITY_BASIS_COMMUNICATION, STATUS_APPROVED, STATUS_PENDING, STATUS_REJECTED


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _media_map(review_names: list[str]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {name: [] for name in review_names}
    if not review_names:
        return result
    rows = frappe.get_all(
        "AOS Review Image",
        filters={"parenttype": "AOS Review", "parent": ["in", review_names]},
        fields=["parent", "media", "idx"],
        order_by="parent asc, idx asc",
        limit=max(1, len(review_names) * 5),
    )
    attachments = [
        (str(row.media or "").strip(), str(row.parent or "").strip())
        for row in rows
        if row.media and row.parent
    ]
    urls = MediaService().get_public_attachment_url_map(
        attachments,
        purpose="review_image",
        attached_doctype="AOS Review",
        attached_field="review_images",
    )
    for row in rows:
        media_id = str(row.media or "").strip()
        review_name = str(row.parent or "").strip()
        url = urls.get((media_id, review_name))
        if media_id and url:
            result.setdefault(review_name, []).append({"id": media_id, "url": url})
    return result


def _reaction_map(review_names: list[str], viewer: str | None) -> dict[str, str]:
    if not viewer or viewer == "Guest" or not review_names:
        return {}
    rows = frappe.get_all(
        "AOS Review Reaction",
        filters={"review": ["in", review_names], "user": viewer},
        fields=["review", "reaction"],
        limit=max(1, len(review_names)),
    )
    return {str(row.review): str(row.reaction) for row in rows}


def _ad_public_id_map(ad_names: list[str]) -> dict[str, str]:
    unique = sorted({name for name in ad_names if name})
    if not unique:
        return {}
    rows = frappe.get_all(
        "AOS Ad",
        filters={"name": ["in", unique]},
        fields=["name", "public_id"],
        limit=max(1, len(unique)),
    )
    return {str(row.name): str(row.public_id or "") for row in rows if row.public_id}


def _verification_map(users: list[str]) -> dict[str, bool]:
    unique = sorted({user for user in users if user})
    if not unique:
        return {}
    rows = frappe.get_all(
        "AOS Profile",
        filters={"user": ["in", unique]},
        fields=["user", "is_verified"],
        limit=max(1, len(unique)),
    )
    return {str(row.user): bool(row.is_verified) for row in rows}


def serialize_reviews(
    rows: Iterable[Any],
    *,
    viewer: str | None = None,
    include_private: bool = False,
) -> list[dict[str, Any]]:
    """Serialize Reviews using one common shape across every Reviews surface."""

    source = list(rows)
    review_names = [str(_get(row, "name") or "") for row in source if _get(row, "name")]
    reviewers = [str(_get(row, "reviewer") or "") for row in source if _get(row, "reviewer")]
    ad_names = [str(_get(row, "ad") or "") for row in source if _get(row, "ad")]

    identities = get_user_display_map(reviewers)
    verification = _verification_map(reviewers)
    media = _media_map(review_names)
    reactions = _reaction_map(review_names, viewer)
    ad_public_ids = _ad_public_id_map(ad_names)

    result: list[dict[str, Any]] = []
    for row in source:
        review_name = str(_get(row, "name") or "")
        public_id = str(_get(row, "public_id") or "")
        reviewer_user = str(_get(row, "reviewer") or "")
        ad_name = str(_get(row, "ad") or "")
        identity = identities.get(reviewer_user, {})
        status = str(_get(row, "status") or "")
        author_deleted = bool(identity.get("is_deleted"))
        item: dict[str, Any] = {
            "id": public_id,
            "ad_id": ad_public_ids.get(ad_name),
            "rating": int(float(_get(row, "rating") or 0)),
            "title": str(_get(row, "title") or ""),
            "comment": str(_get(row, "comment") or ""),
            "author": {
                "account_id": identity.get("account_id"),
                "display_name": identity.get("display_name") or "AOS User",
                "avatar": identity.get("avatar"),
                "is_deleted": author_deleted,
                "is_verified": bool(verification.get(reviewer_user)) if not author_deleted else False,
            },
            "media": media.get(review_name, []),
            "like_count": max(0, int(_get(row, "like_count") or 0)),
            "dislike_count": max(0, int(_get(row, "dislike_count") or 0)),
            "viewer_reaction": reactions.get(review_name),
            "verified_interaction": str(_get(row, "eligibility_basis") or "") == ELIGIBILITY_BASIS_COMMUNICATION,
            "created_at": _get(row, "creation"),
            "updated_at": _get(row, "modified"),
            "edited_at": _get(row, "edited_on") or None,
            "is_edited": bool(int(_get(row, "edit_count") or 0)),
            "version": str(_get(row, "modified") or ""),
        }
        if include_private and viewer == reviewer_user:
            item.update(
                {
                    "status": status,
                    "moderation_reason_code": "REVIEW_REJECTED" if status == STATUS_REJECTED else None,
                    "moderation_message": (
                        "This review was not approved. You may edit and resubmit it."
                        if status == STATUS_REJECTED
                        else None
                    ),
                    "can_edit": status in {STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED},
                    "can_delete": status != "Withdrawn",
                }
            )
        result.append(item)
    return result
