from __future__ import annotations

import frappe

from aos.services.push_service import PushService


class NotificationService:
    """
    Central notification orchestrator.

    Responsibilities:
    - Create AOS Notification record
    - Send push notification (FCM)
    """

    # CORE
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
            return

        # Prevent self-notifications
        if actor and actor == user:
            return

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
    ):
        """
        Deliver notification via push.
        """
        PushService.send_to_user(
            user=user,
            title=title,
            body=body,
            data=payload,
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
    ):
        if not user:
            return

        payload = payload or {}

        # 1. Save notification
        cls._create_notification(
            user=user,
            type=type,
            title=title,
            body=body,
            actor=actor,
            payload=payload,
        )

        # 2. Push notification
        cls._deliver(
            user=user,
            event=event or type,
            title=title,
            body=body,
            payload=payload,
        )

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
        cls.notify(
            user=user,
            type="message",
            title="New Message",
            body=f"{sender}: {preview}",
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
    ):
        cls.notify(
            user=user,
            type="call",
            title="Incoming Call",
            body=f"{caller} is calling you",
            actor=caller,
            payload={
                "call_id": call_id,
                "caller": caller,
                "call_type": call_type,
            },
            event="aos_incoming_call",
        )

    @classmethod
    def notify_missed_call(
        cls,
        *,
        user: str,
        caller: str,
        call_id: str,
    ):
        cls.notify(
            user=user,
            type="missed_call",
            title="Missed Call",
            body=f"You missed a call from {caller}",
            actor=caller,
            payload={
                "call_id": call_id,
                "caller": caller,
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
        cls.notify(
            user=user,
            type="follow",
            title="New Follower",
            body=f"{follower} started following you",
            actor=follower,
            payload={"follower": follower},
            event="aos_follow",
        )

    # ADS
    @classmethod
    def notify_ad_approved(cls, *, user: str, ad_id: str, title: str | None = None):
        cls.notify(
            user=user,
            type="ad_approved",
            title="Ad Approved",
            body=f"Your ad '{title}' has been approved" if title else "Your ad has been approved",
            payload={"ad_id": ad_id},
            event="aos_ad_approved",
        )

    @classmethod
    def notify_ad_rejected(cls, *, user: str, ad_id: str, title: str | None = None):
        cls.notify(
            user=user,
            type="ad_rejected",
            title="Ad Rejected",
            body=f"Your ad '{title}' was rejected" if title else "Your ad was rejected",
            payload={"ad_id": ad_id},
            event="aos_ad_rejected",
        )

    @classmethod
    def notify_ad_expired(cls, *, user: str, ad_id: str, title: str | None = None):
        cls.notify(
            user=user,
            type="ad_expired",
            title="Ad Expired",
            body=f"Your ad '{title}' has expired" if title else "Your ad has expired",
            payload={"ad_id": ad_id},
            event="aos_ad_expired",
        )

    # SELLER VERIFICATION
    @classmethod
    def notify_verification_approved(cls, *, user: str):
        cls.notify(
            user=user,
            type="verification_approved",
            title="Verification Approved ✅",
            body="Your seller verification has been approved.",
            payload={},
            event="aos_verification_approved",
        )

    @classmethod
    def notify_verification_rejected(cls, *, user: str):
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
    def notify_new_short(cls, *, actor: str, short_id: str):
        followers = frappe.get_all(
            "AOS Seller Follow",
            filters={"seller": actor},
            pluck="follower",
        )

        if not followers:
            return

        title = "New Short 🎬"
        body = f"{actor} posted a new short"

        for user in followers:
            if not user:
                continue

            cls.notify(
                user=user,
                type="new_short",
                title=title,
                body=body,
                actor=actor,
                payload={"short_id": short_id},
                event="aos_new_short",
            )

    @classmethod
    def notify_short_like(cls, *, user: str, actor: str, short_id: str):
        cls.notify(
            user=user,
            type="short_like",
            title="New Like ❤️",
            body=f"{actor} liked your short",
            actor=actor,
            payload={"short_id": short_id},
            event="aos_short_like",
        )

    @classmethod
    def notify_short_comment(cls, *, user: str, actor: str, short_id: str):
        cls.notify(
            user=user,
            type="short_comment",
            title="New Comment",
            body=f"{actor} commented on your short",
            actor=actor,
            payload={"short_id": short_id},
            event="aos_short_comment",
        )

    @classmethod
    def notify_comment_reply(cls, *, user: str, actor: str, comment_id: str):
        cls.notify(
            user=user,
            type="comment_reply",
            title="New Reply",
            body=f"{actor} replied to your comment",
            actor=actor,
            payload={"comment_id": comment_id},
            event="aos_comment_reply",
        )

    # LIVE
    @classmethod
    def notify_live_started(
        cls,
        *,
        user: str,
        seller: str,
        live_id: str,
        title: str,
    ):
        cls.notify(
            user=user,
            type="live_started",
            title="Live Started",
            body=f"{seller} is now live: {title}",
            actor=seller,
            payload={
                "live_id": live_id,
                "seller": seller,
            },
            event="aos_live_started",
        )
