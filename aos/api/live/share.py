"""Feature-owned native Live sharing into AOS Chat."""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok
from aos.services.chat.errors import ChatError
from aos.services.chat.service import ChatService
from aos.services.live.errors import LiveError

from .constants import SHARE_LIVE_TO_CHAT_LIMIT_PER_MINUTE_PER_USER


_CHAT_ERROR_MAP: dict[str, tuple[str, int]] = {
    "CHAT_NOT_FOUND": ("LIVE_CHAT_TARGET_NOT_FOUND", 404),
    "CHAT_ACCESS_DENIED": ("LIVE_CHAT_SHARE_FORBIDDEN", 403),
    "CHAT_FORBIDDEN": ("LIVE_CHAT_SHARE_FORBIDDEN", 403),
    "CHAT_INVALID_REQUEST": ("LIVE_CHAT_SHARE_INVALID", 422),
    "CHAT_VALIDATION_ERROR": ("LIVE_CHAT_SHARE_INVALID", 422),
    "CHAT_INPUT_TOO_LARGE": ("LIVE_CHAT_SHARE_INVALID", 413),
}


def share_live_to_chat_impl(**kwargs):
    """Share an active Live as a native Chat message.

    Chat owns message persistence/realtime/notifications; Live owns the
    feature-specific public endpoint and the requirement that a newly shared
    Live is currently active. No web URL is stored as the object reference.
    """

    user, error = require_login()
    if error:
        return error

    limited = rate_limit(
        key=rate_limit_key("live", "share_chat", user),
        ttl_seconds=60,
        limit=SHARE_LIVE_TO_CHAT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many Live shares. Please try again shortly.",
    )
    if limited:
        return limited

    live_id = str(kwargs.get("live_id") or "").strip()
    conversation_id = str(kwargs.get("conversation_id") or "").strip()
    content = str(kwargs.get("message") or kwargs.get("content") or "").strip()
    idempotency_key = str(kwargs.get("idempotency_key") or "").strip() or None

    try:
        message = ChatService().send_live_reference(
            sender=user,
            conversation_id=conversation_id,
            live_id=live_id,
            content=content,
            idempotency_key=idempotency_key,
        )
    except ChatError as exc:
        code, status = _CHAT_ERROR_MAP.get(
            exc.code,
            ("LIVE_CHAT_SHARE_FAILED", 500),
        )
        raise LiveError(
            "Unable to share this Live to chat.",
            code=code,
            http_status=status,
        ) from None
    except frappe.ValidationError:
        raise LiveError(
            "Unable to share this Live to chat.",
            code="LIVE_CHAT_SHARE_INVALID",
            http_status=422,
        ) from None

    return ok(
        "Live shared to chat.",
        data={
            "live_id": live_id,
            "conversation_id": conversation_id,
            "message": message,
        },
    )
