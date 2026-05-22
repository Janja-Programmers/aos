from __future__ import annotations

import frappe
from aos.services.push_service import PushService


class NotificationService:
    """
    Central notification orchestrator.

    Responsibilities:
    - Create AOS Notification record
    - Send push notification (FCM)

    Notes:
    - `user` should always be a real User ID/email recipient.
    - `actor` should also be a real User ID/email when available.
    - Seller docnames should not be passed as notification users/actors unless
      that seller docname is intentionally the same as the User ID.
    """

    # CALL PUSH CONFIG
    INCOMING_CALL_FCM_PRIORITY = "high"
    INCOMING_CALL_FCM_TTL_SECONDS = 30
    INCOMING_CALL_ANDROID_CHANNEL_ID = "aos_calls"
    INCOMING_CALL_ANDROID_NOTIFICATION_PRIORITY = "max"

    # CORE
    @staticmethod
    def _display_name(user: str | None) -> str:
        """
        Resolve a user-facing display name.

        Falls back to the user ID/email if User.full_name is unavailable.
        """
        if not user:
            return ""

        try:
            return frappe.db.get_value("User", user, "full_name") or user
        except Exception:
            return user

    @staticmethod
    def _create_notification(
        *,
        user: str,
        type: str,
        title: str,
        body: str,
        actor: str | None = None,
        payload: dict | None = None,
    ):
        """
        Create notification record.
        """
        if not user:
            return None

        # Prevent self-notifications.
        if actor and actor == user:
            return None

        doc = frappe.get_doc(
            {
                "doctype": "AOS Notification",
                "user": user,
                "type": type,
                "title": title,
                "body": body,
                "actor": actor,
                "payload": payload or {},
            }
        )

        doc.insert(ignore_permissions=True)
        return doc

    @staticmethod
    def _deliver(
        *,
        user: str,
        event: str,
        title: str,
        body: str,
        payload: dict,
        priority: str | None = None,
        ttl_seconds: int | None = None,
        android_channel_id: str | None = None,
        android_notification_priority: str | None = None,
    ):
        """
        Deliver notification via push.

        Optional push options are mainly used by incoming calls.
        PushService translates these into Firebase Admin SDK platform configs.
        """
        push_payload = dict(payload or {})

        if event:
            push_payload["event"] = event

        PushService.send_to_user(
            user=user,
            title=title,
            body=body,
            data=push_payload,
            priority=priority,
            ttl_seconds=ttl_seconds,
            android_channel_id=android_channel_id,
            android_notification_priority=android_notification_priority,
        )

    # GENERIC ENTRY POINT
    @classmethod
    def notify(
        cls,
        *,
        user: str,
        type: str,
        title: str,
        body: str,
        actor: str | None = None,
        payload: dict | None = None,
        event: str | None = None,
        priority: str | None = None,
        ttl_seconds: int | None = None,
        android_channel_id: str | None = None,
        android_notification_priority: str | None = None,
    ):
        if not user:
            return None

        payload = payload or {}

        # Prevent self-notifications before both DB + push.
        if actor and actor == user:
            return None

        # 1. Save notification.
        doc = cls._create_notification(
            user=user,
            type=type,
            title=title,
            body=body,
            actor=actor,
            payload=payload,
        )

        # 2. Push notification.
        cls._deliver(
            user=user,
            event=event or type,
            title=title,
            body=body,
            payload=payload,
            priority=priority,
            ttl_seconds=ttl_seconds,
            android_channel_id=android_channel_id,
            android_notification_priority=android_notification_priority,
        )

        return doc

    # CHAT
    @classmethod
    def notify_new_message(
        cls,
        *,
        user: str,
        sender: str,
        conversation_id: str,
        preview: str,
    ):
        sender_name = cls._display_name(sender)

        cls.notify(
            user=user,
            type="message",
            title="New Message",
            body=f"{sender_name}: {preview}",
            actor=sender,
            payload={
                "conversation_id": conversation_id,
                "sender": sender,
            },
            event="aos_new_message",
        )

    # CALLS
    @classmethod
    def notify_incoming_call(
        cls,
        *,
        user: str,
        caller: str,
        call_id: str,
        call_type: str,
        payload: dict | None = None,
    ):
        caller_name = cls._display_name(caller)

        call_payload = dict(payload or {})

        # Minimal fallback only. Do not overwrite rich payload from call.py.
        call_payload.setdefault("event", "aos_incoming_call")
        call_payload.setdefault("type", "incoming_call")
        call_payload.setdefault("notification_type", "incoming_call")
        call_payload.setdefault("call_id", call_id)
        call_payload.setdefault("id", call_id)
        call_payload.setdefault("caller", caller)
        call_payload.setdefault("call_type", call_type)
        call_payload.setdefault("caller_display_name", caller_name)

        cls.notify(
            user=user,
            type="call",
            title="Incoming Call",
            body=f"{caller_name} is calling you",
            actor=caller,
            payload=call_payload,
            event="aos_incoming_call",
            priority=cls.INCOMING_CALL_FCM_PRIORITY,
            ttl_seconds=cls.INCOMING_CALL_FCM_TTL_SECONDS,
            android_channel_id=cls.INCOMING_CALL_ANDROID_CHANNEL_ID,
            android_notification_priority=cls.INCOMING_CALL_ANDROID_NOTIFICATION_PRIORITY,
        )

    @classmethod
    def notify_missed_call(
        cls,
        *,
        user: str,
        caller: str,
        call_id: str,
    ):
        caller_name = cls._display_name(caller)

        cls.notify(
            user=user,
            type="missed_call",
            title="Missed Call",
            body=f"You missed a call from {caller_name}",
            actor=caller,
            payload={
                "call_id": call_id,
                "caller": caller,
                "type": "missed_call",
                "notification_type": "missed_call",
            },
            event="aos_missed_call",
        )

    # FOLLOW
    @classmethod
    def notify_follow(
        cls,
        *,
        user: str,
        follower: str,
    ):
        follower_name = cls._display_name(follower)

        cls.notify(
            user=user,
            type="follow",
            title="New Follower",
            body=f"{follower_name} started following you",
            actor=follower,
            payload={"follower": follower},
            event="aos_follow",
        )

    # ADS
    @classmethod
    def notify_ad_approved(
        cls,
        *,
        user: str,
        ad_id: str,
        title: str | None = None,
    ):
        cls.notify(
            user=user,
            type="ad_approved",
            title="Ad Approved",
            body=(
                f"Your ad '{title}' has been approved"
                if title
                else "Your ad has been approved"
            ),
            payload={"ad_id": ad_id},
            event="aos_ad_approved",
        )

    @classmethod
    def notify_ad_rejected(
        cls,
        *,
        user: str,
        ad_id: str,
        title: str | None = None,
    ):
        cls.notify(
            user=user,
            type="ad_rejected",
            title="Ad Rejected",
            body=(
                f"Your ad '{title}' was rejected"
                if title
                else "Your ad was rejected"
            ),
            payload={"ad_id": ad_id},
            event="aos_ad_rejected",
        )

    @classmethod
    def notify_ad_expired(
        cls,
        *,
        user: str,
        ad_id: str,
        title: str | None = None,
    ):
        cls.notify(
            user=user,
            type="ad_expired",
            title="Ad Expired",
            body=(
                f"Your ad '{title}' has expired"
                if title
                else "Your ad has expired"
            ),
            payload={"ad_id": ad_id},
            event="aos_ad_expired",
        )

    # SELLER VERIFICATION
    @classmethod
    def notify_verification_approved(
        cls,
        *,
        user: str,
    ):
        cls.notify(
            user=user,
            type="verification_approved",
            title="Verification Approved ✅",
            body="Your seller verification has been approved.",
            payload={},
            event="aos_verification_approved",
        )

    @classmethod
    def notify_verification_rejected(
        cls,
        *,
        user: str,
    ):
        cls.notify(
            user=user,
            type="verification_rejected",
            title="Verification Rejected",
            body="Your seller verification was rejected.",
            payload={},
            event="aos_verification_rejected",
        )

    # SHORTS
    @classmethod
    def notify_new_short(
        cls,
        *,
        actor: str,
        short_id: str,
    ):
        """
        Notify followers when a creator publishes a visible short.

        `actor` must be the short creator/poster User ID, usually AOS Short.owner.
        Do not pass AOS Short.seller here because seller is optional shop context.
        """
        if not actor or not short_id:
            return

        followers = frappe.get_all(
            "AOS Follow",
            filters={"following_user": actor},
            pluck="follower_user",
        )

        if not followers:
            return

        actor_name = cls._display_name(actor)

        title = "New Short 🎬"
        body = f"{actor_name} posted a new short"

        for user in followers:
            if not user:
                continue

            cls.notify(
                user=user,
                type="new_short",
                title=title,
                body=body,
                actor=actor,
                payload={
                    "short_id": short_id,
                    "actor": actor,
                },
                event="aos_new_short",
            )

    @classmethod
    def notify_short_like(
        cls,
        *,
        user: str,
        actor: str,
        short_id: str,
    ):
        """
        Notify a short owner that their short was liked.

        `user` should be AOS Short.owner.
        `actor` should be the user who liked the short.
        """
        actor_name = cls._display_name(actor)

        cls.notify(
            user=user,
            type="short_like",
            title="New Like ❤️",
            body=f"{actor_name} liked your short",
            actor=actor,
            payload={
                "short_id": short_id,
                "actor": actor,
            },
            event="aos_short_like",
        )

    @classmethod
    def notify_short_comment(
        cls,
        *,
        user: str,
        actor: str,
        short_id: str,
        content: str | None = None,
    ):
        """
        Notify a short owner that their short received a comment.

        `user` should be AOS Short.owner.
        `actor` should be the user who commented.
        """
        preview = (content or "").strip()
        actor_name = cls._display_name(actor)

        cls.notify(
            user=user,
            type="short_comment",
            title="New Comment",
            body=(
                f"{actor_name} commented: {preview[:80]}"
                if preview
                else f"{actor_name} commented on your short"
            ),
            actor=actor,
            payload={
                "short_id": short_id,
                "actor": actor,
                "content": preview,
            },
            event="aos_short_comment",
        )

    @classmethod
    def notify_comment_reply(
        cls,
        *,
        user: str,
        actor: str,
        comment_id: str,
        short_id: str | None = None,
        content: str | None = None,
    ):
        """
        Notify a comment owner that someone replied.

        `user` should be AOS Short Comment.user.
        `actor` should be the user who replied.
        """
        preview = (content or "").strip()
        actor_name = cls._display_name(actor)

        payload = {
            "comment_id": comment_id,
            "actor": actor,
            "content": preview,
        }

        if short_id:
            payload["short_id"] = short_id

        cls.notify(
            user=user,
            type="comment_reply",
            title="New Reply",
            body=(
                f"{actor_name} replied: {preview[:80]}"
                if preview
                else f"{actor_name} replied to your comment"
            ),
            actor=actor,
            payload=payload,
            event="aos_comment_reply",
        )

    # LIVE
    @classmethod
    def notify_live_started(
        cls,
        *,
        user: str,
        host_user: str,
        live_id: str,
        title: str,
    ):
        """
        Notify a follower that a creator/host started a live stream.

        `user` is the recipient.
        `host_user` is the live host / creator User ID.
        """
        host_name = cls._display_name(host_user)
        live_title = (title or "").strip()

        cls.notify(
            user=user,
            type="live_started",
            title="Live Started",
            body=(
                f"{host_name} is now live: {live_title}"
                if live_title
                else f"{host_name} is now live"
            ),
            actor=host_user,
            payload={
                "live_id": live_id,
                "host_user": host_user,
            },
            event="aos_live_started",
        )
