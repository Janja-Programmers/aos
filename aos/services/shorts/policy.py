"""Canonical Shorts visibility and authorization policy."""

from __future__ import annotations

from typing import Any

import frappe

from aos.services.social.repository import SocialRepository

from .constants import (
    SHORT_AUDIENCE_EVERYONE,
    SHORT_AUDIENCE_FOLLOWERS,
    SHORT_AUDIENCE_FRIENDS,
    SHORT_AUDIENCE_ONLY_ME,
)

_VISIBLE_STATUSES = {"ready"}
_VISIBLE_VISIBILITY = {"visible"}
_VISIBLE_APPROVAL = {"auto_approved", "approved", ""}


def _get(short: Any, field: str, default: Any = None) -> Any:
    if isinstance(short, dict):
        return short.get(field, default)
    return getattr(short, field, default)


def normalize_user(user: str | None) -> str | None:
    value = str(user or "").strip()
    return None if not value or value == "Guest" else value


def creator_is_available(user: str | None) -> bool:
    user = normalize_user(user)
    if not user:
        return False
    row = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
    if not row or not int(row.get("enabled") or 0):
        return False
    profile_status = frappe.db.get_value("AOS Profile", {"user": user}, "account_status")
    return str(profile_status or "active").strip().lower() not in {
        "deleted", "disabled", "suspended", "deactivated", "restricted"
    }


def can_view(short: Any, *, viewer: str | None) -> bool:
    viewer = normalize_user(viewer)
    owner = normalize_user(_get(short, "owner"))
    if not owner:
        return False
    if viewer == owner:
        return str(_get(short, "status", "")).lower() != "deleted" and str(
            _get(short, "visibility_status", "")
        ).lower() != "deleted"
    if str(_get(short, "status", "")).lower() not in _VISIBLE_STATUSES:
        return False
    if str(_get(short, "visibility_status", "")).lower() not in _VISIBLE_VISIBILITY:
        return False
    approval = str(_get(short, "approval_status", "") or "").lower()
    if approval not in _VISIBLE_APPROVAL:
        return False
    if not creator_is_available(owner):
        return False
    if viewer:
        _outgoing, _incoming, block_map = SocialRepository().relationship_sets(
            viewer=viewer, targets=[owner]
        )
        if any(block_map.get(owner, (False, False))):
            return False
    audience = str(_get(short, "audience", SHORT_AUDIENCE_EVERYONE) or SHORT_AUDIENCE_EVERYONE).lower()
    if audience == SHORT_AUDIENCE_EVERYONE:
        return True
    if not viewer or audience == SHORT_AUDIENCE_ONLY_ME:
        return False
    if audience == SHORT_AUDIENCE_FOLLOWERS:
        return bool(
            frappe.db.exists("AOS Follow", {"follower_user": viewer, "following_user": owner})
        )
    if audience == SHORT_AUDIENCE_FRIENDS:
        return bool(
            frappe.db.exists("AOS Follow", {"follower_user": viewer, "following_user": owner})
            and frappe.db.exists("AOS Follow", {"follower_user": owner, "following_user": viewer})
        )
    return False


def audience_sql(viewer: str | None, *, short_alias: str = "s") -> tuple[str, tuple[Any, ...]]:
    """Return parameterized visibility SQL for feeds and list endpoints."""
    viewer = normalize_user(viewer)
    base = f"""
        AND {short_alias}.status = 'ready'
        AND {short_alias}.visibility_status = 'visible'
        AND COALESCE({short_alias}.approval_status, '') IN ('', 'auto_approved', 'approved')
        AND EXISTS (
            SELECT 1 FROM `tabUser` creator
            WHERE creator.name = {short_alias}.owner AND creator.enabled = 1
        )
        AND NOT EXISTS (
            SELECT 1 FROM `tabAOS Profile` creator_profile
            WHERE creator_profile.user = {short_alias}.owner
              AND LOWER(COALESCE(creator_profile.account_status, 'active'))
                  IN ('deleted', 'disabled', 'suspended', 'deactivated', 'restricted')
        )
    """
    if not viewer:
        return base + f" AND {short_alias}.audience = %s", (SHORT_AUDIENCE_EVERYONE,)
    return base + f"""
        AND NOT EXISTS (
            SELECT 1 FROM `tabAOS User Block` social_block
            WHERE social_block.status = 'Active'
              AND ((social_block.blocker_user = %s AND social_block.blocked_user = {short_alias}.owner)
                OR (social_block.blocked_user = %s AND social_block.blocker_user = {short_alias}.owner))
        )
        AND (
            {short_alias}.audience = %s OR {short_alias}.owner = %s
            OR ({short_alias}.audience = %s AND EXISTS (
                SELECT 1 FROM `tabAOS Follow` af
                WHERE af.follower_user = %s AND af.following_user = {short_alias}.owner
            ))
            OR ({short_alias}.audience = %s AND EXISTS (
                SELECT 1 FROM `tabAOS Follow` af1
                WHERE af1.follower_user = %s AND af1.following_user = {short_alias}.owner
            ) AND EXISTS (
                SELECT 1 FROM `tabAOS Follow` af2
                WHERE af2.follower_user = {short_alias}.owner AND af2.following_user = %s
            ))
        )
    """, (
        viewer, viewer, SHORT_AUDIENCE_EVERYONE, viewer,
        SHORT_AUDIENCE_FOLLOWERS, viewer, SHORT_AUDIENCE_FRIENDS, viewer, viewer,
    )


def can_comment(short: Any, *, viewer: str | None) -> bool:
    return bool(normalize_user(viewer) and can_view(short, viewer=viewer) and int(_get(short, "allow_comments", 0) or 0))


def can_download(short: Any, *, viewer: str | None) -> bool:
    return bool(can_view(short, viewer=viewer) and int(_get(short, "allow_downloads", 0) or 0))


def filter_viewable_rows(rows: list[dict[str, Any]], *, viewer: str | None, limit: int | None = None) -> list[dict[str, Any]]:
    """Batch-filter rows without per-Short account or relationship queries."""
    from aos.services.social.serializers import relationship_map

    viewer = normalize_user(viewer)
    owners = sorted({normalize_user(row.get("owner")) for row in rows or [] if normalize_user(row.get("owner"))})
    if not owners:
        return []

    user_rows = frappe.db.sql(
        "SELECT name, enabled FROM `tabUser` WHERE name IN %(owners)s",
        {"owners": tuple(owners)},
        as_dict=True,
    )
    enabled = {row.get("name") for row in user_rows if int(row.get("enabled") or 0)}
    profile_rows = frappe.db.sql(
        "SELECT user, account_status FROM `tabAOS Profile` WHERE user IN %(owners)s",
        {"owners": tuple(owners)},
        as_dict=True,
    )
    unavailable = {
        row.get("user")
        for row in profile_rows
        if str(row.get("account_status") or "Active").strip().lower()
        in {"deleted", "disabled", "suspended", "deactivated", "restricted"}
    }
    relationships = relationship_map(repository=SocialRepository(), viewer=viewer, targets=owners) if viewer else {}

    result: list[dict[str, Any]] = []
    for row in rows or []:
        owner = normalize_user(row.get("owner"))
        status = str(row.get("status") or "").lower()
        visibility = str(row.get("visibility_status") or "").lower()
        approval = str(row.get("approval_status") or "").lower()
        if not owner:
            continue
        if viewer == owner:
            allowed = status != "deleted" and visibility != "deleted"
        else:
            if status != "ready" or visibility != "visible" or approval not in _VISIBLE_APPROVAL:
                continue
            if owner not in enabled or owner in unavailable:
                continue
            relationship = relationships.get(owner) or {}
            if viewer and bool(relationship.get("is_blocked")):
                continue
            audience = str(row.get("audience") or SHORT_AUDIENCE_EVERYONE).lower()
            allowed = audience == SHORT_AUDIENCE_EVERYONE
            if viewer and audience == SHORT_AUDIENCE_FOLLOWERS:
                allowed = bool(relationship.get("is_following"))
            elif viewer and audience == SHORT_AUDIENCE_FRIENDS:
                allowed = bool(relationship.get("is_friend"))
            elif audience == SHORT_AUDIENCE_ONLY_ME:
                allowed = False
        if allowed:
            result.append(row)
            if limit and len(result) >= limit:
                break
    return result
