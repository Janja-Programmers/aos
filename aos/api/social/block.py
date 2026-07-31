"""Thin user-block API boundaries backed by the canonical Social service."""

from __future__ import annotations

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.services.social.api import run_social_api
from aos.services.social.observability import social_log
from aos.services.social.service import SocialService

from .activity import record_block_user_activity
from .constants import (
    BLOCK_USER_LIMIT_PER_MINUTE_PER_USER,
    GET_BLOCK_STATUS_LIMIT_PER_MINUTE_PER_USER,
    LIST_BLOCKED_USERS_LIMIT_PER_MINUTE_PER_USER,
    UNBLOCK_USER_LIMIT_PER_MINUTE_PER_USER,
)


def block_user_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=f"aos:block:user:{current_user}",
        ttl_seconds=60,
        limit=BLOCK_USER_LIMIT_PER_MINUTE_PER_USER,
        message="Too many block requests. Please try again shortly.",
    )
    if limited:
        social_log("block", outcome="rejected", reason="rate_limit")
        return limited

    def operation():
        data = SocialService().block(actor=current_user, payload=kwargs, activity_callback=record_block_user_activity)
        return ok("User blocked successfully." if data.get("changed") else "User already blocked.", data=data)

    return run_social_api(operation, fallback="Failed to block user.", log_title="AOS Block User Failed", operation_name="block")


def unblock_user_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=f"aos:unblock:user:{current_user}",
        ttl_seconds=60,
        limit=UNBLOCK_USER_LIMIT_PER_MINUTE_PER_USER,
        message="Too many unblock requests. Please try again shortly.",
    )
    if limited:
        social_log("unblock", outcome="rejected", reason="rate_limit")
        return limited

    def operation():
        data = SocialService().unblock(actor=current_user, payload=kwargs)
        return ok("User unblocked successfully." if data.get("changed") else "User is not blocked.", data=data)

    return run_social_api(operation, fallback="Failed to unblock user.", log_title="AOS Unblock User Failed", operation_name="unblock")


def get_block_status_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=f"aos:block:status:user:{current_user}",
        ttl_seconds=60,
        limit=GET_BLOCK_STATUS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many block-status requests. Please try again shortly.",
    )
    if limited:
        social_log("block_status", outcome="rejected", reason="rate_limit")
        return limited
    return run_social_api(
        lambda: ok("Block status fetched successfully.", data=SocialService().block_status(actor=current_user, payload=kwargs)),
        fallback="Failed to get block status.",
        log_title="AOS Get Block Status Failed",
        operation_name="block_status",
    )


def list_blocked_users_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=f"aos:block:list:user:{current_user}",
        ttl_seconds=60,
        limit=LIST_BLOCKED_USERS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many blocked-list requests. Please try again shortly.",
    )
    if limited:
        social_log("blocked_list", outcome="rejected", reason="rate_limit")
        return limited
    return run_social_api(
        lambda: ok("Blocked users fetched successfully.", data=SocialService().blocked_users(actor=current_user, payload=kwargs)),
        fallback="Failed to fetch blocked users.",
        log_title="AOS List Blocked Users Failed",
        operation_name="blocked_list",
    )
