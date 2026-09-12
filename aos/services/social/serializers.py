"""Privacy-safe Social relationship and list serializers."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Iterable

import frappe

from aos.api.shared.formatters import humanize_count, to_non_negative_int
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.accounts.serializers import serialize_internal_identity_map

from .constants import (
    RELATIONSHIP_FOLLOWED_BY,
    RELATIONSHIP_FOLLOWING,
    RELATIONSHIP_FRIENDS,
    RELATIONSHIP_NONE,
)
from .repository import SocialRepository


def _site_secret() -> bytes:
    value = ""
    try:
        value = str(getattr(frappe.local, "conf", {}).get("encryption_key") or "")
    except Exception:
        pass
    if not value:
        try:
            value = str((frappe.get_site_config() or {}).get("encryption_key") or "")
        except Exception:
            pass
    if not value:
        raise RuntimeError("Social opaque-reference secret is unavailable")
    return value.encode("utf-8")


def opaque_block_ref(name: Any) -> str:
    digest = hmac.new(_site_secret(), str(name or "").encode("utf-8"), hashlib.sha256).hexdigest()[:24].upper()
    return f"BLK-{digest}"


def relationship_payload(
    *,
    target: str,
    is_self: bool,
    outgoing: bool,
    incoming: bool,
    blocked_by_me: bool,
    blocked_me: bool,
    target_account_id: str | None = None,
) -> dict[str, Any]:
    blocked = bool(blocked_by_me or blocked_me)
    if blocked:
        outgoing = False
        incoming = False
    is_friend = bool(outgoing and incoming)
    if is_friend:
        status = RELATIONSHIP_FRIENDS
    elif outgoing:
        status = RELATIONSHIP_FOLLOWING
    elif incoming:
        status = RELATIONSHIP_FOLLOWED_BY
    else:
        status = RELATIONSHIP_NONE
    if is_self:
        action = "You"
    elif blocked_by_me:
        action = "Unblock"
    elif blocked_me:
        action = "Unavailable"
    elif status == RELATIONSHIP_FRIENDS:
        action = "Friends"
    elif status == RELATIONSHIP_FOLLOWING:
        action = "Following"
    elif status == RELATIONSHIP_FOLLOWED_BY:
        action = "Follow Back"
    else:
        action = "Follow"
    block_status = (
        "mutual_block" if blocked_by_me and blocked_me else
        "blocked_by_me" if blocked_by_me else
        "blocked_me" if blocked_me else "none"
    )
    return {
        "account_id": target_account_id or public_account_id_for_user(target),
        "is_self": bool(is_self),
        "is_following": bool(outgoing),
        "is_followed_by": bool(incoming),
        "is_friend": is_friend,
        "relationship_status": status,
        "action_label": action,
        "is_blocked_by_me": bool(blocked_by_me),
        "has_blocked_me": bool(blocked_me),
        "is_blocked": blocked,
        "block_status": block_status,
        "can_follow": not blocked and not is_self,
        "can_message": not blocked and not is_self,
        "can_call": not blocked and not is_self,
        "can_view_profile": not blocked,
    }


def relationship_map(
    *,
    repository: SocialRepository,
    viewer: str,
    targets: Iterable[str],
    identities: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    unique = sorted({str(target) for target in targets if target})
    outgoing, incoming, blocks = repository.relationship_sets(viewer=viewer, targets=unique)
    identities = identities if identities is not None else serialize_internal_identity_map(unique)
    result: dict[str, dict[str, Any]] = {}
    for target in unique:
        blocked_by_me, blocked_me = blocks.get(target, (False, False))
        identity = identities.get(target) or {}
        available = bool(identity.get("enabled")) and not bool(identity.get("is_deleted")) and identity.get("account_status") == "Active"
        projection = relationship_payload(
            target=target,
            target_account_id=identity.get("account_id"),
            is_self=target == viewer,
            outgoing=target in outgoing,
            incoming=target in incoming,
            blocked_by_me=blocked_by_me,
            blocked_me=blocked_me,
        )
        if not available and target != viewer:
            projection.update({
                "is_following": False,
                "is_followed_by": False,
                "is_friend": False,
                "relationship_status": RELATIONSHIP_NONE,
                "action_label": "Unavailable",
                "can_follow": False,
                "can_message": False,
                "can_call": False,
                "can_view_profile": False,
            })
        result[target] = projection
    return result


def serialize_users(*, repository: SocialRepository, viewer: str, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = list(rows)
    targets = [str(row.get("user") or "") for row in rows if row.get("user")]
    identities = serialize_internal_identity_map(targets)
    relationships = relationship_map(
        repository=repository,
        viewer=viewer,
        targets=targets,
        identities=identities,
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        target = str(row.get("user") or "")
        identity = identities.get(target) or {}
        if not target or not identity.get("enabled") or identity.get("is_deleted") or identity.get("account_status") != "Active":
            continue
        followers = to_non_negative_int(row.get("total_followers"))
        following = to_non_negative_int(row.get("total_following"))
        friends = to_non_negative_int(row.get("total_friends"))
        item = {
            "account_id": identity.get("account_id") or public_account_id_for_user(target),
            "display_name": identity.get("display_name") or "AOS User",
            "avatar": identity.get("avatar"),
            "is_deleted": False,
            "total_followers": followers,
            "total_followers_display": humanize_count(followers),
            "total_following": following,
            "total_following_display": humanize_count(following),
            "total_friends": friends,
            "total_friends_display": humanize_count(friends),
            "is_verified": bool(row.get("is_verified")),
            "followed_at": row.get("followed_at"),
            **relationships.get(target, relationship_payload(
                target=target, is_self=target == viewer, outgoing=False, incoming=False,
                blocked_by_me=False, blocked_me=False,
            )),
        }
        if row.get("followed_back_at"):
            item["followed_back_at"] = row.get("followed_back_at")
        items.append(item)
    return items


def serialize_blocked_users(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = list(rows)
    targets = [str(row.get("user") or "") for row in rows if row.get("user")]
    identities = serialize_internal_identity_map(targets)
    items: list[dict[str, Any]] = []
    for row in rows:
        target = str(row.get("user") or "")
        identity = identities.get(target) or {}
        unavailable = not identity.get("enabled") or bool(identity.get("is_deleted")) or identity.get("account_status") != "Active"
        items.append(
            {
                "id": opaque_block_ref(row.get("block_name")),
                "account_id": identity.get("account_id") or public_account_id_for_user(target),
                "display_name": "Deleted account" if unavailable else (identity.get("display_name") or "AOS User"),
                "avatar": None if unavailable else identity.get("avatar"),
                "is_deleted": bool(unavailable),
                "reason": str(row.get("reason") or ""),
                "blocked_at": row.get("blocked_at"),
                "status": "blocked",
            }
        )
    return items
