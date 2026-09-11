"""Authenticated public-safe web push bootstrap endpoint."""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.services.accounts.http import set_private_no_store
from aos.services.notifications.observability import notification_log
from aos.services.notifications.validation import NotificationInputError, reject_unknown_fields
from aos.services.notifications.web_push import WebPushConfigurationError, get_web_push_config

from .constants import GET_PUSH_CONFIG_LIMIT_PER_MINUTE_PER_USER


def get_push_config_impl(**kwargs):
    set_private_no_store()
    current_user, err = require_login()
    if err:
        return err
    rl = rate_limit(
        key=f"aos:push:config:user:{current_user}",
        ttl_seconds=60,
        limit=GET_PUSH_CONFIG_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl
    try:
        reject_unknown_fields(kwargs, allowed=set())
    except NotificationInputError:
        return fail(
            "Invalid web push configuration request.",
            error="INVALID_NOTIFICATION_INPUT",
            http_status=400,
        )

    try:
        config = get_web_push_config()
    except WebPushConfigurationError as exc:
        notification_log(
            "notification.web_push_config_unavailable",
            outcome="disabled",
            reason=exc.__class__.__name__,
        )
        try:
            frappe.log_error(
                "Web push is enabled but its public Firebase configuration is incomplete.",
                "AOS Notification web push configuration",
            )
        except Exception:
            pass
        return ok("Web push configuration fetched.", data={"enabled": False})

    return ok("Web push configuration fetched.", data=config.public_payload())
