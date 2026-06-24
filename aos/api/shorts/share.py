"""Share APIs for Shorts.

Handles:
- share short into AOS chat
- create external share links
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import get_url, now_datetime

from aos.api.shared.auth import require_login, current_user
from aos.api.shared.blocking import ensure_not_blocked
from aos.api.shared.formatters import humanize_count
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id
from aos.services.notification_service import NotificationService

from aos.api.chat.message import (
    _fetch_ads_bulk,
    _fetch_reply_messages_bulk,
    _fetch_shorts_bulk,
    _fetch_users,
    _serialize_attachments_bulk,
    _serialize_message,
    _validate_short_reference,
)
from aos.api.chat.preview import set_conversation_preview_for_new_message
from aos.api.chat.presence import publish_presence_update_to_peers
from aos.services.seller_response_metrics import (
    enqueue_conversation_response_metrics_refresh,
)

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
        ],
        as_dict=True,
    )

    if not short:
        return None, fail("Short not found.", code="NOT_FOUND")

    if short.status != "ready" or short.visibility_status != "visible":
        return None, fail("Short not found.", code="NOT_FOUND")

    if not can_view_short(short, current_user=viewer):
        return None, fail("Short not found.", code="NOT_FOUND")

    return short, None


def _normalize_channel(channel: str | None) -> tuple[str | None, object | None]:
    channel = (channel or "copy_link").strip().lower()
    if channel not in VALID_SHARE_CHANNELS:
        return None, fail("Invalid share channel.", code="VALIDATION_ERROR")
    return channel, None


def _increment_share_count(
    *,
    short_id: str,
    user: str | None,
    session_id: str | None,
    channel: str,
    conversation_id: str | None = None,
) -> int:
    frappe.db.sql(
        """
        UPDATE `tabAOS Short`
        SET share_count = share_count + 1
        WHERE name = %s
        """,
        (short_id,),
    )

    metadata: dict[str, Any] = {"channel": channel}
    if conversation_id:
        metadata["conversation_id"] = conversation_id

    frappe.get_doc(
        {
            "doctype": "AOS Short Event",
            "short": short_id,
            "user": user,
            "session_id": None if user else session_id,
            "event_type": "share",
            "source": channel,
            "metadata": json.dumps(metadata),
        }
    ).insert(ignore_permissions=True)

    return int(frappe.db.get_value("AOS Short", short_id, "share_count") or 0)


def _get_conversation_row(conv_id: str):
    return frappe.db.get_value(
        "AOS Conversation",
        conv_id,
        ["name", "participant_1", "participant_2"],
        as_dict=True,
    )


def _get_receiver(conv, sender: str) -> str:
    return conv.participant_2 if conv.participant_1 == sender else conv.participant_1


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
                code="VALIDATION_ERROR",
            )

        short, err = _get_short_for_share(short_id, viewer=viewer)
        if err:
            return err

        share_count = _increment_share_count(
            short_id=short_id,
            user=viewer,
            session_id=session_id,
            channel=channel,
        )

        frappe.db.commit()
        frappe.enqueue(RANKING_TASK, short_id=short_id, queue="short")

        return ok(
            "Share link created.",
            data={
                "short_id": short_id,
                "share_url": _build_share_url(short_id),
                "channel": channel,
                "metrics": {
                    "share_count": share_count,
                    "share_count_display": humanize_count(share_count),
                },
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "create_short_share_link failed")
        frappe.db.rollback()
        return fail("Failed to create share link", code="INTERNAL_ERROR")


def share_short_to_chat_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:share_chat:user:{user}",
        ttl_seconds=60,
        limit=SHARE_SHORT_TO_CHAT_LIMIT_PER_MINUTE,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    conversation_id, err = require_id(
        kwargs.get("conversation_id"),
        "conversation_id",
    )
    if err:
        return err

    note = (kwargs.get("message") or kwargs.get("content") or "").strip()

    try:
        short, err = _get_short_for_share(short_id, viewer=user)
        if err:
            return err

        conv = _get_conversation_row(conversation_id)
        if not conv:
            return fail("Conversation not found.", code="NOT_FOUND")

        if user not in (conv.participant_1, conv.participant_2):
            return fail("Not allowed.", code="PERMISSION_DENIED")

        receiver = _get_receiver(conv, user)

        short_error = _validate_short_reference(
            short_id,
            viewer=user,
            recipients=[receiver],
        )
        if short_error:
            return short_error

        block_err = ensure_not_blocked(
            current_user=user,
            target_user=receiver,
            action="message",
        )
        if block_err:
            return block_err

        now = now_datetime()

        msg = frappe.new_doc("AOS Message")
        msg.conversation = conversation_id
        msg.sender = user
        msg.message_type = "short" if not note else "mixed"
        msg.content = note or None
        msg.short = short_id
        msg.has_attachments = 0
        msg.is_forwarded = 0
        msg.insert(ignore_permissions=True)

        preview = note or f"Shared a short: {_short_preview(short)}"

        set_conversation_preview_for_new_message(
            conversation_id=conversation_id,
            sender=user,
            preview=preview,
            sent_at=now,
        )

        unread_field = "unread_count_2" if user == conv.participant_1 else "unread_count_1"
        frappe.db.sql(
            f"""
            UPDATE `tabAOS Conversation`
            SET
                is_active_1 = 1,
                is_active_2 = 1,
                {unread_field} = COALESCE({unread_field}, 0) + 1
            WHERE name = %s
            """,
            (conversation_id,),
        )

        share_count = _increment_share_count(
            short_id=short_id,
            user=user,
            session_id=None,
            channel="chat",
            conversation_id=conversation_id,
        )

        reply_map = _fetch_reply_messages_bulk([])
        user_map = _fetch_users([user])
        ad_map = _fetch_ads_bulk([])
        short_map = _fetch_shorts_bulk([short_id], viewer=user)
        attachments_map = _serialize_attachments_bulk([msg.name])

        message_payload = _serialize_message(
            msg,
            attachments_map=attachments_map,
            user_map=user_map,
            ad_map=ad_map,
            short_map=short_map,
            current_user=user,
            reply_map=reply_map,
            is_starred=False,
            reactions=[],
            my_reaction=None,
        )
        message_payload["conversation_id"] = conversation_id

        frappe.publish_realtime(
            event="aos_new_message",
            message={"conversation_id": conversation_id, "message": message_payload},
            user=receiver,
        )

        NotificationService.notify_new_message(
            user=receiver,
            sender=user,
            conversation_id=conversation_id,
            preview=preview,
        )

        publish_presence_update_to_peers(user)
        enqueue_conversation_response_metrics_refresh(
            participant_1=conv.participant_1,
            participant_2=conv.participant_2,
        )

        frappe.db.commit()
        frappe.enqueue(RANKING_TASK, short_id=short_id, queue="short")

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
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "share_short_to_chat failed")
        frappe.db.rollback()
        return fail("Failed to share short to chat", code="INTERNAL_ERROR")
