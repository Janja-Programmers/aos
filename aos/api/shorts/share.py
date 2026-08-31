"""Share APIs for Shorts.

Handles:
- share short into AOS chat
- create external share links
"""

from __future__ import annotations

import json
import time
from typing import Any

import frappe
from frappe.utils import get_url

from aos.api.shared.auth import require_login, current_user
from aos.api.shared.db import is_duplicate_entry_error
from aos.api.shared.formatters import humanize_count
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.validators import require_id
from aos.services.shorts.analytics import event_key
from aos.services.shorts.repository import ShortsRepository
from aos.services.shorts.recommendation import RecommendationService

from aos.services.chat.errors import ChatError
from aos.services.chat.service import ChatService

from aos.api.shorts.constants import (
    CREATE_SHORT_SHARE_LINK_LIMIT_PER_MINUTE_PER_IP,
    SHARE_SHORT_TO_CHAT_LIMIT_PER_MINUTE,
)
from aos.api.shorts.visibility import can_view_short

RANKING_TASK = "aos.api.shorts.tasks.update_short_score_task"

VALID_SHARE_CHANNELS = {
    "copy_link",
    "whatsapp",
    "facebook",
    "instagram",
    "sms",
    "system_share",
    "chat",
}


def _get_optional_viewer() -> str | None:
    user = current_user()
    if not user or user == "Guest":
        return None
    return user


def _get_short_for_share(short_id: str, *, viewer: str | None):
    short = frappe.db.get_value(
        "AOS Short",
        short_id,
        [
            "name",
            "owner",
            "status",
            "visibility_status",
            "audience",
            "caption",
            "thumbnail_url",
            "playback_url",
            "processed_file_url",
        ],
        as_dict=True,
    )

    if not short:
        return None, fail("Short not found.", error="NOT_FOUND")

    if short.status != "ready" or short.visibility_status != "visible":
        return None, fail("Short not found.", error="NOT_FOUND")

    if not can_view_short(short, current_user=viewer):
        return None, fail("Short not found.", error="NOT_FOUND")

    return short, None


def _normalize_channel(channel: str | None) -> tuple[str | None, object | None]:
    channel = (channel or "copy_link").strip().lower()
    if channel not in VALID_SHARE_CHANNELS:
        return None, fail("Invalid share channel.", error="VALIDATION_ERROR")
    return channel, None


def _increment_share_count(
    *,
    short_id: str,
    user: str | None,
    session_id: str | None,
    channel: str,
    conversation_id: str | None = None,
    client_event_id: object | None = None,
) -> int:

    metadata: dict[str, Any] = {"channel": channel}
    if conversation_id:
        metadata["conversation_id"] = conversation_id

    actor = f"user:{user}" if user else f"session:{session_id}"
    dedupe_id = str(client_event_id or f"bucket:{int(time.time() // 30)}")[:140]
    counted = True
    try:
        frappe.get_doc(
            {
                "doctype": "AOS Short Event",
                "short": short_id,
                "user": user,
                "session_id": None if user else session_id,
                "event_type": "share",
                "source": channel,
                "metadata": json.dumps(metadata),
                "event_key": event_key(
                    event_type="share", short_id=short_id, actor_key=actor, client_event_id=dedupe_id
                ),
            }
        ).insert(ignore_permissions=True)
    except Exception as exc:
        if is_duplicate_entry_error(exc):
            counted = False
        else:
            raise
    if counted:
        ShortsRepository().increment_counter(short_id, "share_count", 1)
        RecommendationService.invalidate_profile(user=user, session_id=session_id)
    return int(frappe.db.get_value("AOS Short", short_id, "share_count") or 0)


def _short_preview(short) -> str:
    caption = (short.get("caption") or "").strip()
    return caption[:80] if caption else "[Short]"


def _build_share_url(short_id: str) -> str:
    return get_url(f"/shorts/{short_id}")


def create_short_share_link_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:share_link:ip:{request_ip()}",
        ttl_seconds=60,
        limit=CREATE_SHORT_SHARE_LINK_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    channel, err = _normalize_channel(kwargs.get("channel"))
    if err:
        return err

    try:
        viewer = _get_optional_viewer()
        session_id = str(kwargs.get("session_id") or "").strip() or None

        if not viewer and not session_id:
            return fail(
                "session_id is required for guest share links.",
                error="VALIDATION_ERROR",
            )

        short, err = _get_short_for_share(short_id, viewer=viewer)
        if err:
            return err

        share_count = _increment_share_count(
            short_id=short_id,
            user=viewer,
            session_id=session_id,
            channel=channel,
            client_event_id=kwargs.get("event_id"),
        )

        frappe.enqueue(RANKING_TASK, short_id=short_id, queue="short", enqueue_after_commit=True)

        return ok(
            "Share link created.",
            data={
                "short_id": short_id,
                "share_url": _build_share_url(short_id),
                "playable_url": short.get("playback_url") or short.get("processed_file_url") or "",
                "thumbnail_url": short.get("thumbnail_url") or "",
                "preview_text": _short_preview(short),
                "channel": channel,
                "metrics": {
                    "share_count": share_count,
                    "share_count_display": humanize_count(share_count),
                },
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error("Shorts operation failed.", "create_short_share_link failed")
        return fail("Failed to create share link", error="INTERNAL_ERROR")


def share_short_to_chat_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("shorts", "share_chat", user),
        ttl_seconds=60,
        limit=SHARE_SHORT_TO_CHAT_LIMIT_PER_MINUTE,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    conversation_id, err = require_id(kwargs.get("conversation_id"), "conversation_id")
    if err:
        return err

    note = (kwargs.get("message") or kwargs.get("content") or "").strip()
    event_id = str(kwargs.get("event_id") or "").strip() or None

    try:
        short, err = _get_short_for_share(short_id, viewer=user)
        if err:
            return err

        try:
            message_payload = ChatService().send_short_reference(
                sender=user,
                conversation_id=conversation_id,
                short_id=short_id,
                content=note,
                idempotency_key=event_id,
            )
        except ChatError as exc:
            mapping = {
                "CHAT_NOT_FOUND": ("Conversation not found.", "NOT_FOUND", 404),
                "CHAT_ACCESS_DENIED": ("Not allowed.", "PERMISSION_DENIED", 403),
                "CHAT_FORBIDDEN": ("Not allowed.", "PERMISSION_DENIED", 403),
                "CHAT_INVALID_REQUEST": ("Invalid share request.", "VALIDATION_ERROR", 422),
                "CHAT_VALIDATION_ERROR": ("Invalid share request.", "VALIDATION_ERROR", 422),
            }
            message, code, status = mapping.get(
                exc.code,
                ("Failed to share short to chat.", "INTERNAL_ERROR", 500),
            )
            return fail(message, error=code, http_status=status)

        share_count = _increment_share_count(
            short_id=short_id,
            user=user,
            session_id=None,
            channel="chat",
            conversation_id=conversation_id,
            client_event_id=event_id or message_payload.get("id"),
        )

        frappe.enqueue(RANKING_TASK, short_id=short_id, queue="short", enqueue_after_commit=True)

        return ok(
            "Short shared to chat.",
            data={
                "message": message_payload,
                "metrics": {
                    "share_count": share_count,
                    "share_count_display": humanize_count(share_count),
                },
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error("Shorts operation failed.", "share_short_to_chat failed")
        return fail("Failed to share short to chat", error="INTERNAL_ERROR")

