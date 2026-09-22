"""Canonical Shorts visibility and action policy.

Processing readiness, moderation eligibility, account availability and Social
relationships are deliberately independent checks.  No caller should infer
public visibility from one ambiguous status field.
"""
from __future__ import annotations

from typing import Any
import frappe

from aos.services.social.repository import SocialRepository
from aos.services.social.serializers import relationship_map

from .constants import REUSE_SEGMENT, REUSE_SIDE_BY_SIDE

_PUBLIC_LIFECYCLE = "Published"
_PUBLIC_MODERATION = "Approved"
_READY_PROCESSING = {"Ready", "Not Required"}


def _get(row: Any, key: str, default=None):
    return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)


def normalize_user(user: str | None) -> str | None:
    value = str(user or "").strip()
    return None if not value or value == "Guest" else value


def technically_ready(short: Any) -> bool:
    return str(_get(short, "processing_status") or "") in _READY_PROCESSING


def distribution_eligible(short: Any) -> bool:
    return (
        str(_get(short, "lifecycle_status") or "") == _PUBLIC_LIFECYCLE
        and str(_get(short, "moderation_status") or "") == _PUBLIC_MODERATION
        and technically_ready(short)
    )


def creator_is_available(user: str) -> bool:
    if not user:
        return False
    row = frappe.db.get_value(
        "AOS Profile", {"user": user}, ["account_status"], as_dict=True
    )
    enabled = frappe.db.get_value("User", user, "enabled")
    status = str((row or {}).get("account_status") or "Active")
    return bool(int(enabled or 0)) and status == "Active"


def can_view(short: Any, *, viewer: str | None) -> bool:
    viewer = normalize_user(viewer)
    owner = str(_get(short, "owner") or "")
    if viewer and viewer == owner:
        return str(_get(short, "lifecycle_status") or "") != "Deleted"
    if not distribution_eligible(short) or not creator_is_available(owner):
        return False
    audience = str(_get(short, "audience") or "everyone")
    if audience == "everyone":
        return True
    if not viewer or audience == "only_me":
        return False
    rel = relationship_map(repository=SocialRepository(), viewer=viewer, targets=[owner]).get(owner) or {}
    if rel.get("is_blocked"):
        return False
    if audience == "followers":
        return bool(rel.get("is_following"))
    if audience == "friends":
        return bool(rel.get("is_friend"))
    return False


def can_comment(short: Any, *, viewer: str | None) -> bool:
    return bool(normalize_user(viewer) and int(_get(short, "allow_comments", 0) or 0) and can_view(short, viewer=viewer))


def can_download(short: Any, *, viewer: str | None) -> bool:
    viewer = normalize_user(viewer)
    if viewer and viewer == str(_get(short, "owner") or ""):
        return str(_get(short, "lifecycle_status") or "") != "Deleted"
    return bool(int(_get(short, "allow_downloads", 0) or 0) and can_view(short, viewer=viewer))


def can_reuse(short: Any, *, viewer: str | None, reuse_type: str | None = None) -> bool:
    if not can_view(short, viewer=viewer):
        return False
    if not int(_get(short, "allow_reuse", 0) or 0):
        return False
    if reuse_type == REUSE_SIDE_BY_SIDE:
        return bool(int(_get(short, "allow_side_by_side", 0) or 0))
    if reuse_type == REUSE_SEGMENT:
        return bool(int(_get(short, "allow_segment_reuse", 0) or 0))
    return True


def filter_viewable_rows(rows: list[dict[str, Any]], *, viewer: str | None, limit: int | None = None) -> list[dict[str, Any]]:
    """Batch-filter feed rows using one identity and one Social relationship projection."""
    from aos.services.accounts.serializers import serialize_internal_identity_map

    viewer = normalize_user(viewer)
    owners = sorted({str(row.get("owner") or "") for row in rows if row.get("owner")})
    identities = serialize_internal_identity_map(owners)
    relationships = relationship_map(repository=SocialRepository(), viewer=viewer, targets=owners, identities=identities) if viewer else {}
    result: list[dict[str, Any]] = []
    for row in rows:
        owner = str(row.get("owner") or "")
        if viewer and viewer == owner:
            allowed = str(row.get("lifecycle_status") or "") != "Deleted"
        else:
            identity = identities.get(owner) or {}
            if not distribution_eligible(row) or not identity.get("enabled") or identity.get("is_deleted") or identity.get("account_status") != "Active":
                continue
            relation = relationships.get(owner) or {}
            if relation.get("is_blocked"):
                continue
            audience = str(row.get("audience") or "everyone")
            allowed = audience == "everyone"
            if viewer and audience == "followers": allowed = bool(relation.get("is_following"))
            if viewer and audience == "friends": allowed = bool(relation.get("is_friend"))
            if audience == "only_me": allowed = False
        if allowed:
            result.append(row)
            if limit and len(result) >= limit:
                break
    return result

def filter_distributable_rows(
    rows: list[dict[str, Any]],
    *,
    viewer: str | None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Batch-filter rows for a public/discovery feed.

    Unlike ``filter_viewable_rows`` this intentionally has no owner bypass:
    drafts, hidden/rejected content, unavailable creators, blocked relations,
    and audience-ineligible rows must never leak from a stale feed cache even
    when the viewer owns the Short.
    """
    from aos.services.accounts.serializers import serialize_internal_identity_map

    viewer = normalize_user(viewer)
    owners = sorted({str(row.get("owner") or "") for row in rows if row.get("owner")})
    identities = serialize_internal_identity_map(owners)
    relationships = (
        relationship_map(
            repository=SocialRepository(),
            viewer=viewer,
            targets=owners,
            identities=identities,
        )
        if viewer
        else {}
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        owner = str(row.get("owner") or "")
        identity = identities.get(owner) or {}
        if (
            not distribution_eligible(row)
            or not identity.get("enabled")
            or identity.get("is_deleted")
            or identity.get("account_status") != "Active"
        ):
            continue
        relation = relationships.get(owner) or {}
        if relation.get("is_blocked"):
            continue
        audience = str(row.get("audience") or "everyone")
        allowed = audience == "everyone"
        if viewer and audience == "followers":
            allowed = bool(relation.get("is_following"))
        if viewer and audience == "friends":
            allowed = bool(relation.get("is_friend"))
        if audience == "only_me":
            allowed = False
        if allowed:
            result.append(row)
            if limit and len(result) >= limit:
                break
    return result

