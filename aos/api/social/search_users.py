"""Thin global user-discovery endpoint."""

from __future__ import annotations

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.api.shared.sql_safety import (
    require_dotted_sql_identifier,
    safe_like_contains,
    safe_like_prefix,
)
from aos.services.social.api import run_social_api
from aos.services.social.observability import social_log
from aos.services.social.service import SocialService

from .activity import record_user_search_activity
from .constants import SEARCH_USERS_LIMIT_PER_MINUTE_PER_USER


def _not_blocked_sql(*, target_expr: str) -> str:
    """Compatibility helper retained for the repository SQL-safety suite."""
    target = require_dotted_sql_identifier(target_expr)
    return f"""
        AND NOT EXISTS (
            SELECT 1 FROM `tabAOS User Block` b
            WHERE b.status = 'Active'
              AND ((b.blocker_user = %(current_user)s AND b.blocked_user = {target})
                OR (b.blocked_user = %(current_user)s AND b.blocker_user = {target}))
        )
    """


def _search_like(value: str) -> str:
    """Compatibility wrapper around the canonical escaped LIKE helper."""
    return safe_like_contains(value)


def _prefix_like(value: str) -> str:
    """Compatibility wrapper around the canonical escaped prefix helper."""
    return safe_like_prefix(value)


def search_users_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=f"aos:social:search_users:user:{current_user}",
        ttl_seconds=60,
        limit=SEARCH_USERS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many search requests. Please try again shortly.",
    )
    if limited:
        social_log("search", outcome="rejected", reason="rate_limit")
        return limited
    return run_social_api(
        lambda: ok(
            "Users fetched successfully.",
            data=SocialService().search(
                actor=current_user,
                payload=kwargs,
                activity_callback=record_user_search_activity,
            ),
        ),
        fallback="Failed to search users.",
        log_title="AOS Search Users Failed",
        operation_name="search",
    )
