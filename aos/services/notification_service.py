from __future__ import annotations

import frappe

from aos.api.shared.db import is_duplicate_entry_error
from aos.api.shared.user_display import get_user_display
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.notification_delivery_service import create_notification_delivery_job
from aos.services.social.constants import MAX_SOCIAL_EVENT_FANOUT
from aos.services.social.repository import SocialRepository


class NotificationService:
    """
    Central notification orchestrator.

    Responsibilities:
    - Create persistent AOS Notification records
    - Queue push notification delivery jobs
    - Queue transient push-only delivery jobs when persistence is not appropriate

    Notes:
    - `user` should always be a real User ID/email recipient.
    - `actor` should also be a real User ID/email when available.
    - Seller docnames should not be passed as notification users/actors unless
      that seller docname is intentionally the same as the User ID.
    - Incoming-call events are transient and must not be stored as
      AOS Notification records.
    - Missed-call events remain persistent notifications.
    """

    # CALL PUSH CONFIG
    INCOMING_CALL_FCM_PRIORITY = "high"
    INCOMING_CALL_FCM_TTL_SECONDS = 30
    INCOMING_CALL_ANDROID_CHANNEL_ID = "aos_calls"
    INCOMING_CALL_ANDROID_NOTIFICATION_PRIORITY = "max"

    # CORE
    @staticmethod
    def _display_name(user: str | None) -> str:
        """Resolve a display-safe user-facing name."""
        if not user:
            return ""

        try:
            return get_user_display(user).get("display_name") or user
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
        dedupe_key: str | None = None,
    ):
        """
        Create a persistent AOS Notification record.
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
                "dedupe_key": str(dedupe_key or "").strip() or None,
            }
        )

        try:
            doc.insert(ignore_permissions=True)
            return doc
        except Exception as exc:
            if not dedupe_key or not is_duplicate_entry_error(exc):
                raise
            existing = frappe.db.get_value(
                "AOS Notification",
                {"dedupe_key": dedupe_key},
                "name",
            )
            if not existing:
                raise
            existing_doc = frappe.get_doc("AOS Notification", existing)
            existing_doc.flags.aos_dedupe_existing = True
            return existing_doc

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
        notification_id: str | None = None,
    ):
        """
        Deliver a notification or transient event through push.

        Optional push options are mainly used by incoming calls.
        The external notification-delivery worker translates these into provider configs.
        """
        push_payload = dict(payload or {})

        if event:
            push_payload["event"] = event

        try:
            create_notification_delivery_job(
                user=user,
                event=event,
                title=title,
                body=body,
                payload=push_payload,
                notification_id=notification_id,
                delivery_kind="persistent" if notification_id else "transient",
                priority=priority,
                ttl_seconds=ttl_seconds,
                android_channel_id=android_channel_id,
                android_notification_priority=android_notification_priority,
                enqueue=True,
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Notification delivery enqueue failed",
            )

    # GENERIC ENTRY POINTS
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
        dedupe_key: str | None = None,
    ):
        """
        Create a persistent notification and deliver its push notification.
        """
        if not user:
            return None

        payload = payload or {}

        # Prevent self-notifications before both DB persistence and push.
        if actor and actor == user:
            return None

        # 1. Save persistent notification.
        doc = cls._create_notification(
            user=user,
            type=type,
            title=title,
            body=body,
            actor=actor,
            payload=payload,
            dedupe_key=dedupe_key,
        )

        # 2. Deliver push notification only for the newly-created row. A
        # database dedupe conflict returns the authoritative existing document
        # and must not create a second delivery job.
        if not (doc and getattr(doc.flags, "aos_dedupe_existing", False)):
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
                notification_id=doc.name if doc else None,
            )

        return doc

    @classmethod
    def deliver_transient(
        cls,
        *,
        user: str,
        event: str,
        title: str,
        body: str,
        actor: str | None = None,
        payload: dict | None = None,
        priority: str | None = None,
        ttl_seconds: int | None = None,
        android_channel_id: str | None = None,
        android_notification_priority: str | None = None,
    ):
        """
        Deliver a transient push event without creating an
        AOS Notification record.

        Use this for short-lived events such as incoming calls where the event
        should be handled immediately but should not appear in the persistent
        notification inbox.
        """
        if not user:
            return None

        # Prevent self-notifications before push delivery.
        if actor and actor == user:
            return None

        cls._deliver(
            user=user,
            event=event,
            title=title,
            body=body,
            payload=payload or {},
            priority=priority,
            ttl_seconds=ttl_seconds,
            android_channel_id=android_channel_id,
            android_notification_priority=android_notification_priority,
        )

        return None

    # CHAT
    @classmethod
    def notify_new_message(
        cls,
        *,
        user: str,
        sender: str,
        conversation_id: str,
        preview: str,
        message_id: str | None = None,
    ):
        sender_name = cls._display_name(sender)
        sender_account_id = public_account_id_for_user(sender)
        dedupe_key = f"chat_message:{message_id}:{user}" if message_id else None

        return cls.notify(
            user=user,
            type="message",
            title="New Message",
            body=f"{sender_name}: {preview}",
            actor=sender,
            payload={
                "conversation_id": conversation_id,
                "sender": sender_account_id,
                "sender_account_id": sender_account_id,
                "message_id": message_id,
            },
            event="aos_new_message",
            dedupe_key=dedupe_key,
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
        """
        Deliver an incoming-call push event.

        Incoming calls are transient and are not stored as AOS Notification
        records. Only missed calls are stored in the notification inbox.
        """
        caller_name = cls._display_name(caller)
        if not caller_name or caller_name == caller:
            caller_name = "AOS User"
        caller_public_id = public_account_id_for_user(caller)

        call_payload = dict(payload or {})

        # Minimal fallback only. Do not overwrite rich payload from call.py.
        call_payload.setdefault("event", "aos_incoming_call")
        call_payload.setdefault("type", "incoming_call")
        call_payload.setdefault("notification_type", "incoming_call")
        call_payload.setdefault("call_id", call_id)
        call_payload.setdefault("id", call_id)
        call_payload.setdefault("caller", caller_public_id)
        call_payload.setdefault("caller_account_id", caller_public_id)
        call_payload.setdefault("call_type", call_type)
        call_payload.setdefault("caller_display_name", caller_name)

        return cls.deliver_transient(
            user=user,
            event="aos_incoming_call",
            title="Incoming Call",
            body=f"{caller_name} is calling you",
            actor=caller,
            payload=call_payload,
            priority=cls.INCOMING_CALL_FCM_PRIORITY,
            ttl_seconds=cls.INCOMING_CALL_FCM_TTL_SECONDS,
            android_channel_id=cls.INCOMING_CALL_ANDROID_CHANNEL_ID,
            android_notification_priority=(
                cls.INCOMING_CALL_ANDROID_NOTIFICATION_PRIORITY
            ),
        )

    @classmethod
    def notify_missed_call(
        cls,
        *,
        user: str,
        caller: str,
        call_id: str,
    ):
        """
        Create and deliver a persistent missed-call notification.
        """
        caller_name = cls._display_name(caller)
        if not caller_name or caller_name == caller:
            caller_name = "AOS User"
        caller_public_id = public_account_id_for_user(caller)

        return cls.notify(
            user=user,
            type="missed_call",
            title="Missed Call",
            body=f"You missed a call from {caller_name}",
            actor=caller,
            payload={
                "call_id": call_id,
                "caller": caller_public_id,
                "caller_account_id": caller_public_id,
                "type": "missed_call",
                "notification_type": "missed_call",
            },
            event="aos_missed_call",
            dedupe_key=f"call:missed:{call_id}:{user}",
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

        return cls.notify(
            user=user,
            type="follow",
            title="New Follower",
            body=f"{follower_name} started following you",
            actor=follower,
            payload={"follower": public_account_id_for_user(follower)},
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
        return cls.notify(
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
        return cls.notify(
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
        return cls.notify(
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

    # REVIEWS
    @classmethod
    def notify_review_received(
        cls,
        *,
        user: str,
        actor: str,
        review_id: str,
        ad_id: str,
    ):
        actor_name = cls._display_name(actor)
        return cls.notify(
            user=user,
            type="review_received",
            title="New Review",
            body=f"{actor_name} reviewed one of your ads",
            actor=actor,
            payload={"review_id": review_id, "ad_id": ad_id},
            event="aos_review_received",
        )

    @classmethod
    def notify_review_approved(cls, *, user: str, review_id: str, ad_id: str):
        return cls.notify(
            user=user,
            type="review_approved",
            title="Review Published",
            body="Your review is now visible.",
            payload={"review_id": review_id, "ad_id": ad_id},
            event="aos_review_approved",
        )

    @classmethod
    def notify_review_rejected(cls, *, user: str, review_id: str, ad_id: str):
        return cls.notify(
            user=user,
            type="review_rejected",
            title="Review Needs Changes",
            body="Your review was not approved. You can edit and resubmit it.",
            payload={"review_id": review_id, "ad_id": ad_id},
            event="aos_review_rejected",
        )

    # VERIFICATION
    @classmethod
    def notify_verification_approved(
        cls,
        *,
        user: str,
        verification_id: str | None = None,
        decision_token: str | None = None,
    ):
        account_id = public_account_id_for_user(user)
        payload = {"account_id": account_id}
        if verification_id:
            payload["verification_id"] = verification_id
        return cls.notify(
            user=user,
            type="verification_approved",
            title="Verification Approved ✅",
            body="Your verification has been approved.",
            payload=payload,
            event="aos_verification_approved",
            dedupe_key=(
                f"verification_approved:{verification_id}:{decision_token or 'legacy'}"
                if verification_id
                else None
            ),
        )

    @classmethod
    def notify_verification_rejected(
        cls,
        *,
        user: str,
        verification_id: str | None = None,
        decision_token: str | None = None,
    ):
        account_id = public_account_id_for_user(user)
        payload = {"account_id": account_id}
        if verification_id:
            payload["verification_id"] = verification_id
        return cls.notify(
            user=user,
            type="verification_rejected",
            title="Verification Rejected",
            body="Your verification was rejected.",
            payload=payload,
            event="aos_verification_rejected",
            dedupe_key=(
                f"verification_rejected:{verification_id}:{decision_token or 'legacy'}"
                if verification_id
                else None
            ),
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
            return None

        followers = SocialRepository().list_active_followers_for_event(
            target=actor,
            limit=MAX_SOCIAL_EVENT_FANOUT,
        )

        if not followers:
            return None

        actor_name = cls._display_name(actor)
        actor_public_id = public_account_id_for_user(actor)

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
                    "actor": actor_public_id,
                },
                event="aos_new_short",
            )

        return None

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
        actor_public_id = public_account_id_for_user(actor)

        return cls.notify(
            user=user,
            type="short_like",
            title="New Like ❤️",
            body=f"{actor_name} liked your short",
            actor=actor,
            payload={
                "short_id": short_id,
                "actor": actor_public_id,
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
        actor_public_id = public_account_id_for_user(actor)

        return cls.notify(
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
                "actor": actor_public_id,
                "content": preview,
            },
            event="aos_short_comment",
        )

    @classmethod
    def notify_short_mention(
        cls,
        *,
        user: str,
        actor: str,
        short_id: str,
        comment_id: str | None = None,
        source_type: str | None = None,
    ):
        """Notify a user that they were mentioned in a short caption/comment."""
        actor_name = cls._display_name(actor)
        actor_public_id = public_account_id_for_user(actor)

        payload = {
            "short_id": short_id,
            "actor": actor_public_id,
            "source_type": source_type,
        }

        if comment_id:
            payload["comment_id"] = comment_id

        where = "a comment" if source_type in {"comment", "reply"} else "a short"

        return cls.notify(
            user=user,
            type="short_mention",
            title="You were mentioned",
            body=f"{actor_name} mentioned you in {where}",
            actor=actor,
            payload=payload,
            event="aos_short_mention",
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
        actor_public_id = public_account_id_for_user(actor)

        payload = {
            "comment_id": comment_id,
            "actor": actor_public_id,
            "content": preview,
        }

        if short_id:
            payload["short_id"] = short_id

        return cls.notify(
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
        dedupe_key: str | None = None,
    ):
        """
        Notify a follower that a creator/host started a live stream.

        `user` is the recipient.
        `host_user` is the live host / creator User ID.
        """
        try:
            host_name = get_user_display(host_user).get("display_name") or "A creator"
        except Exception:
            host_name = "A creator"
        host_public_id = public_account_id_for_user(host_user)
        live_title = (title or "").strip()

        return cls.notify(
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
                "host_user": host_public_id,
            },
            event="aos_live_started",
            dedupe_key=dedupe_key,
        )
