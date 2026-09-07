"""Privacy-safe Reviews serializers."""

from __future__ import annotations

from typing import Any, Iterable

import frappe

from aos.api.shared.formatters import humanize_count
from aos.api.shared.user_display import get_user_display_map
from aos.services.media.media_service import MediaService

from .constants import STATUS_APPROVED, STATUS_HIDDEN, STATUS_PENDING, STATUS_REJECTED


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _image_map(review_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    result = {review_id: [] for review_id in review_ids}
    if not review_ids:
        return result
    rows = frappe.get_all(
        "AOS Review Image",
        filters={"parenttype": "AOS Review", "parent": ["in", review_ids]},
        fields=["parent", "media", "idx"],
        order_by="parent asc, idx asc",
    )
    media_service = MediaService()
    for row in rows:
        media_id = str(row.media or "").strip()
        url = ""
        if media_id:
            try:
                url = media_service.get_public_url(media_id)
            except Exception:
                url = ""
        if not url:
            continue
        result.setdefault(row.parent, []).append(
            {"media_id": media_id, "media": media_id, "url": url, "image": url}
        )
    return result


def _reaction_map(review_ids: list[str], viewer: str | None) -> dict[str, str]:
    if not viewer or not review_ids:
        return {}
    rows = frappe.get_all(
        "AOS Review Reaction",
        filters={"review": ["in", review_ids], "user": viewer},
        fields=["review", "reaction"],
    )
    return {row.review: row.reaction for row in rows}


def serialize_reviews(
    rows: Iterable[Any],
    *,
    viewer: str | None = None,
    include_private: bool = False,
) -> list[dict[str, Any]]:
    rows = list(rows)
    review_ids = [str(_get(row, "name") or "") for row in rows if _get(row, "name")]
    reviewers = [str(_get(row, "reviewer") or "") for row in rows if _get(row, "reviewer")]
    identities = get_user_display_map(reviewers)
    images = _image_map(review_ids)
    reactions = _reaction_map(review_ids, viewer)
    result: list[dict[str, Any]] = []
    for row in rows:
        review_id = str(_get(row, "name") or "")
        reviewer_user = str(_get(row, "reviewer") or "")
        identity = identities.get(reviewer_user, {})
        image_items = images.get(review_id, [])
        status = str(_get(row, "status") or "")
        item = {
            "id": review_id,
            "ad_id": _get(row, "ad"),
            "rating": int(float(_get(row, "rating") or 0)),
            "title": str(_get(row, "title") or ""),
            "comment": str(_get(row, "comment") or ""),
            "created_at": _get(row, "creation"),
            "edited_at": _get(row, "edited_on") or None,
            "is_edited": bool(int(_get(row, "edit_count") or 0)),
            "like_count": max(0, int(_get(row, "like_count") or 0)),
            "like_count_display": humanize_count(_get(row, "like_count") or 0),
            "dislike_count": max(0, int(_get(row, "dislike_count") or 0)),
            "dislike_count_display": humanize_count(_get(row, "dislike_count") or 0),
            "user_reaction": reactions.get(review_id),
            "verified_interaction": str(_get(row, "eligibility_basis") or "") == "communication",
            "reviewer": {
                "account_id": identity.get("account_id"),
                "display_name": identity.get("display_name") or "AOS User",
                "avatar": identity.get("avatar"),
                "is_deleted": bool(identity.get("is_deleted")),
                "is_live": bool(identity.get("is_live")) if not identity.get("is_deleted") else False,
                "live_id": identity.get("live_id") if not identity.get("is_deleted") else None,
            },
            "images": [image["url"] for image in image_items],
            "image_items": image_items,
        }
        if include_private and viewer == reviewer_user:
            item.update(
                {
                    "status": status,
                    "moderation_status": status,
                    "moderation_reason_code": "REVIEW_REJECTED" if status == STATUS_REJECTED else None,
                    # Provider classifications and internal moderator notes stay
                    # private. Clients receive only a stable code and bounded
                    # product-safe guidance.
                    "moderation_message": (
                        "This review was not approved. You may edit and resubmit it."
                        if status == STATUS_REJECTED
                        else None
                    ),
                    "can_edit": status in {STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED},
                    "can_delete": status not in {"Withdrawn"},
                }
            )
        elif status == STATUS_HIDDEN:
            continue
        result.append(item)
    return result
