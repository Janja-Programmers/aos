"""
Short visibility helpers.

This module centralizes all audience/privacy checks for AOS Shorts.

Audience rules:
- everyone: visible to anyone
- followers: visible to users who follow the short creator
- friends: visible to mutual followers
- only_me: visible only to the creator

Important:
Always use can_view_short() before exposing a short through direct lookup,
feeds, saved shorts, liked shorts, share links, downloads, or profile tabs.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.auth import optional_active_user
from aos.api.shared.blocking import is_blocked_between
from aos.services.social.repository import SocialRepository
from aos.api.shorts.constants import (
    DEFAULT_SHORT_AUDIENCE,
    SHORT_AUDIENCE_EVERYONE,
    SHORT_AUDIENCE_FOLLOWERS,
    SHORT_AUDIENCE_FRIENDS,
    SHORT_AUDIENCE_ONLY_ME,
    VALID_SHORT_AUDIENCES,
)


def normalize_user(user: str | None) -> str | None:
    """
    Normalize user identity.

    Frappe uses 'Guest' for unauthenticated sessions.
    For visibility checks, Guest should behave like None.
    """
    if not user:
        return None

    user = str(user).strip()

    if not user or user == "Guest":
        return None

    return user


def get_current_user_or_none() -> str | None:
    """
    Return the current active logged-in user, or None for Guest/stale sessions.
    """
    return optional_active_user()


def normalize_audience(value: str | None) -> str:
    """
    Normalize audience value.

    Missing/invalid DB values are treated as DEFAULT_SHORT_AUDIENCE so older
    records do not accidentally become inaccessible.
    """
    audience = str(value or DEFAULT_SHORT_AUDIENCE).strip().lower()

    if audience not in VALID_SHORT_AUDIENCES:
        return DEFAULT_SHORT_AUDIENCE

    return audience


def get_short_owner(short: Any) -> str | None:
    """
    Resolve short owner safely from either a Frappe doc or dict-like object.
    """
    if not short:
        return None

    if isinstance(short, dict):
        return normalize_user(short.get("owner"))

    return normalize_user(getattr(short, "owner", None))


def get_short_audience(short: Any) -> str:
    """
    Resolve short audience safely from either a Frappe doc or dict-like object.
    """
    if not short:
        return DEFAULT_SHORT_AUDIENCE

    if isinstance(short, dict):
        return normalize_audience(short.get("audience"))

    return normalize_audience(getattr(short, "audience", None))


def is_following_user(*, follower_user: str, following_user: str) -> bool:
    """
    Check whether follower_user follows following_user.

    Uses the user-centric AOS Follow model:
    - follower_user: current viewer
    - following_user: short creator
    """
    follower_user = normalize_user(follower_user)
    following_user = normalize_user(following_user)

    if not follower_user or not following_user:
        return False

    if follower_user == following_user:
        return True

    if is_blocked_between(follower_user, following_user):
        return False

    return SocialRepository().follow_exists(
        follower=follower_user,
        target=following_user,
    )


def are_friends(*, user_a: str, user_b: str) -> bool:
    """
    Check whether two users are friends.

    In AOS, friends means mutual follow:
    - user_a follows user_b
    - user_b follows user_a
    """
    user_a = normalize_user(user_a)
    user_b = normalize_user(user_b)

    if not user_a or not user_b:
        return False

    if user_a == user_b:
        return True

    return is_following_user(
        follower_user=user_a,
        following_user=user_b,
    ) and is_following_user(
        follower_user=user_b,
        following_user=user_a,
    )


def can_view_short(short: Any, current_user: str | None = None) -> bool:
    """
    Return whether current_user can view the given short.

    Rules:
    - Owner can always view their own short.
    - everyone can be viewed by anyone, including Guest.
    - followers requires logged-in viewer following creator.
    - friends requires mutual follow.
    - only_me requires owner.
    """
    owner = get_short_owner(short)
    audience = get_short_audience(short)
    current_user = normalize_user(current_user)

    if not owner:
        return False

    # Owner can always view their own short.
    if current_user and current_user == owner:
        return True

    # Blocking is stronger than public audience. This prevents public-feed,
    # direct-link, saved, liked, and profile-tab leakage in either direction.
    if current_user and is_blocked_between(current_user, owner):
        return False

    if audience == SHORT_AUDIENCE_EVERYONE:
        return True

    # Guests cannot view restricted shorts.
    if not current_user:
        return False

    if audience == SHORT_AUDIENCE_FOLLOWERS:
        return is_following_user(
            follower_user=current_user,
            following_user=owner,
        )

    if audience == SHORT_AUDIENCE_FRIENDS:
        return are_friends(
            user_a=current_user,
            user_b=owner,
        )

    if audience == SHORT_AUDIENCE_ONLY_ME:
        return False

    return False


def can_view_short_record(
    *,
    short_id: str,
    current_user: str | None = None,
) -> bool:
    """
    Convenience helper for checking visibility by short id.

    Use this when only the short id is available.
    """
    if not short_id:
        return False

    if not frappe.db.exists("AOS Short", short_id):
        return False

    short = frappe.get_doc("AOS Short", short_id)
    return can_view_short(short, current_user=current_user)


def filter_viewable_shorts(
    shorts: list[Any],
    current_user: str | None = None,
    *,
    limit: int | None = None,
) -> list[Any]:
    """
    Filter a list of short docs/dicts to only those visible to current_user.

    This is useful as a final safety layer after SQL/feed filtering.
    """
    current_user = normalize_user(current_user)
    result = []

    for short in shorts or []:
        if can_view_short(short, current_user=current_user):
            result.append(short)

            if limit and len(result) >= limit:
                break

    return result
