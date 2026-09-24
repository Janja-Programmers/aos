from __future__ import annotations

import uuid

import frappe

from aos.api.shared.db import is_duplicate_entry_error
from aos.api.shared.user_display import get_user_display
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.notifications.delivery import create_notification_delivery_job
from aos.services.notifications.contracts import (
    MAX_NOTIFICATION_BODY_LENGTH,
    MAX_NOTIFICATION_DEDUPE_KEY_LENGTH,
    MAX_NOTIFICATION_TITLE_LENGTH,
    NotificationContractError,
    canonical_event,
    contract_for,
    validate_persistent_payload,
)
from aos.services.notifications.observability import notification_log
from aos.services.notifications.policy import (
    persistent_notification_suppression_reason,
    transient_recipient_suppression_reason,
)
from aos.services.notifications.realtime import publish_created_after_commit
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

    @staticmethod
    def _rollback_savepoint(savepoint: str) -> None:
        try:
            frappe.db.rollback(save_point=savepoint)
        except Exception:
            # Notification infrastructure never owns the caller's full transaction.
            pass

    @staticmethod
    def _normalize_copy(*, title: str, body: str, dedupe_key: str | None) -> tuple[str, str, str | None]:
        # Builders own notification copy, but some copy includes bounded user
        # content (message previews, ad titles, comments). Normalize it before
        # persistence/provider delivery instead of dropping an otherwise-valid
        # business notification solely because its preview was long.
        title = " ".join(str(title or "").replace("\x00", "").split())
        body = " ".join(str(body or "").replace("\x00", "").split())
        dedupe = str(dedupe_key or "").replace("\x00", "").strip() or None
        if not title:
            raise NotificationContractError("Invalid notification title.")
        if not body:
            raise NotificationContractError("Invalid notification body.")
        title = title[:MAX_NOTIFICATION_TITLE_LENGTH]
        body = body[:MAX_NOTIFICATION_BODY_LENGTH]
        if dedupe and len(dedupe) > MAX_NOTIFICATION_DEDUPE_KEY_LENGTH:
            raise NotificationContractError("Notification dedupe key is too long.")
        return title, body, dedupe

    # CORE
    @staticmethod
    def _display_name(user: str | None) -> str:
        """Resolve a display-safe user-facing name."""
        if not user:
            return ""

        try:
            return get_user_display(user).get("display_name") or "AOS User"
        except Exception:
            # Internal User.name is commonly an email and must never become a
            # lock-screen/inbox display fallback.
            return "AOS User"

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
        idempotency_key: str | None = None,
    ):
        """Create the durable push-delivery job inside the caller transaction."""
        push_payload = dict(payload or {})
        if event:
            push_payload["event"] = event

        return create_notification_delivery_job(
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
            idempotency_key=idempotency_key,
            enqueue=True,
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
        """Persist one canonical notification and its outbox intent atomically.

        Notification is infrastructure: failures roll back only this notification
        savepoint and never the surrounding business operation.
        """
        user = str(user or "").strip()
        actor = str(actor or "").strip() or None
        notification_type = str(type or "").strip()
        if not user:
            return None

        try:
            contract = contract_for(notification_type)
            resolved_event = str(event or contract.event).strip()
            if resolved_event != canonical_event(notification_type):
                raise NotificationContractError("Notification event does not match its type.")
            clean_payload = validate_persistent_payload(notification_type, payload or {})
            title, body, dedupe_key = cls._normalize_copy(
                title=title, body=body, dedupe_key=dedupe_key
            )
        except NotificationContractError as exc:
            notification_log(
                "notification.intent_rejected",
                notification_type=notification_type,
                outcome="rejected",
                reason=exc.__class__.__name__,
            )
            return None

        policy_reason = persistent_notification_suppression_reason(
            user=user, notification_type=notification_type, actor=actor
        )
        if policy_reason:
            notification_log(
                "notification.intent_suppressed",
                account_id=public_account_id_for_user(user),
                notification_type=notification_type,
                outcome="suppressed",
                reason=policy_reason,
            )
            return None

        savepoint = f"aos_notification_{uuid.uuid4().hex[:12]}"
        frappe.db.savepoint(savepoint)
        try:
            doc = cls._create_notification(
                user=user,
                type=notification_type,
                title=title,
                body=body,
                actor=actor,
                payload=clean_payload,
                dedupe_key=dedupe_key,
            )
            if not doc:
                cls._rollback_savepoint(savepoint)
                return None

            # Always ensure the durable delivery job/outbox exists. The job has a
            # deterministic idempotency key derived from the persistent
            # notification, so a duplicate producer retry repairs partial
            # divergence without creating a second push job.
            cls._deliver(
                user=user,
                event=resolved_event,
                title=title,
                body=body,
                payload=clean_payload,
                priority=priority,
                ttl_seconds=ttl_seconds,
                android_channel_id=android_channel_id,
                android_notification_priority=android_notification_priority,
                notification_id=doc.name,
            )
            existing = bool(getattr(doc.flags, "aos_dedupe_existing", False))
            if not existing:
                # Realtime is a post-commit foreground transport only. It must
                # never escape a rolled-back business mutation and duplicate
                # producer retries must not emit duplicate creation events.
                publish_created_after_commit(user=user, notification_id=doc.name)
            notification_log(
                "notification.intent_deduplicated" if existing else "notification.intent_created",
                notification_id=doc.name,
                account_id=public_account_id_for_user(user),
                notification_type=notification_type,
                category=contract.category,
                outcome="deduplicated" if existing else "created",
            )
            return doc
        except Exception as exc:
            cls._rollback_savepoint(savepoint)
            notification_log(
                "notification.intent_failed",
                account_id=public_account_id_for_user(user),
                notification_type=notification_type,
                category=contract.category,
                outcome="failed",
                reason=exc.__class__.__name__,
            )
            try:
                frappe.log_error(
                    frappe.get_traceback(),
                    "AOS Notification intent failed",
                )
            except Exception:
                pass
            return None

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
        idempotency_key: str | None = None,
    ):
        """Create a transient push-only outbox intent without inbox persistence."""
        user = str(user or "").strip()
        actor = str(actor or "").strip() or None
        event = str(event or "").strip()
        if not user or not event:
            return None
        if event != "aos_incoming_call":
            notification_log(
                "notification.transient_rejected",
                outcome="rejected",
                reason="unsupported_event",
            )
            return None
        if transient_recipient_suppression_reason(user=user, actor=actor):
            return None

        try:
            title, body, _ = cls._normalize_copy(title=title, body=body, dedupe_key=None)
        except NotificationContractError:
            return None

        savepoint = f"aos_notification_transient_{uuid.uuid4().hex[:10]}"
        frappe.db.savepoint(savepoint)
        try:
            job = cls._deliver(
                user=user,
                event=event,
                title=title,
                body=body,
                payload=payload or {},
                priority=priority,
                ttl_seconds=ttl_seconds,
                android_channel_id=android_channel_id,
                android_notification_priority=android_notification_priority,
                idempotency_key=idempotency_key,
            )
            notification_log(
                "notification.transient_queued",
                job_id=getattr(job, "name", None),
                account_id=public_account_id_for_user(user),
                delivery_kind="transient",
                outcome="queued" if job else "disabled",
            )
            return None
        except Exception as exc:
            cls._rollback_savepoint(savepoint)
            notification_log(
                "notification.transient_failed",
                account_id=public_account_id_for_user(user),
                delivery_kind="transient",
                outcome="failed",
                reason=exc.__class__.__name__,
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
        private_preview: bool = False,
    ):
        """Persist one message notification.

        Locked-chat recipients receive no sender identity or message text in the
        notification record/push payload. The conversation/message IDs remain as
        opaque navigation handles and are re-authorized by Chat on open.
        """
        sender_name = cls._display_name(sender)
        sender_account_id = public_account_id_for_user(sender)
        dedupe_key = f"chat_message:{message_id}:{user}" if message_id else None
        if private_preview:
            body = "New message in a locked chat"
            actor = None
            payload = {"conversation_id": conversation_id, "message_id": message_id, "private_preview": True}
        else:
            body = f"{sender_name}: {preview}"
            actor = sender
            payload = {
                "conversation_id": conversation_id,
                "sender": sender_account_id,
                "sender_account_id": sender_account_id,
                "message_id": message_id,
            }

        return cls.notify(
            user=user,
            type="message",
            title="New Message",
            body=body,
            actor=actor,
            payload=payload,
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
        call_payload.pop("id", None)
        call_payload.setdefault("call_id", call_id)
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
            idempotency_key=f"incoming_call:{call_id}:{user}",
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
        dedupe_key: str | None = None,
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
            dedupe_key=dedupe_key,
        )

    # SELLERS
    @classmethod
    def notify_seller_status_changed(
        cls,
        *,
        user: str,
        seller_id: str,
        old_status: str,
        new_status: str,
        reason_code: str,
        transition_token: str,
    ):
        copy = {
            "Suspended": ("Seller Suspended", "Your seller storefront has been suspended."),
            "Active": ("Seller Reactivated", "Your seller storefront is active again."),
            "Closed": ("Seller Closed", "Your seller storefront has been closed."),
        }
        title, body = copy.get(
            str(new_status or ""),
            ("Seller Status Updated", "Your seller storefront status has changed."),
        )
        return cls.notify(
            user=user,
            type="seller_status_changed",
            title=title,
            body=body,
            payload={
                "seller_id": seller_id,
                "status": new_status,
                "reason_code": reason_code,
            },
            event="aos_seller_status_changed",
            dedupe_key=f"seller:status:{seller_id}:{new_status}:{transition_token}",
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
        reason: str | None = None,
    ):
        clean_reason = " ".join(str(reason or "").replace("\x00", "").split()).strip()
        base = (
            f"Your ad '{title}' was rejected"
            if title
            else "Your ad was rejected"
        )
        body = f"{base}. Reason: {clean_reason}" if clean_reason else base
        payload = {"ad_id": ad_id}
        if clean_reason:
            payload["reason"] = clean_reason
        return cls.notify(
            user=user,
            type="ad_rejected",
            title="Ad Rejected",
            body=body,
            payload=payload,
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
            dedupe_key=f"review:received:{review_id}:{user}",
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
            dedupe_key=f"review:approved:{review_id}:{user}",
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
            dedupe_key=f"review:rejected:{review_id}:{user}",
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
                f"verification:approved:{verification_id}:{decision_token}"
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
                f"verification:rejected:{verification_id}:{decision_token}"
                if verification_id
                else None
            ),
        )

    # MEDIA
    @classmethod
    def notify_media_processing_failed(cls, *, user: str, job_id: str, source_media_id: str):
        return cls.notify(
            user=user,
            type="media_processing_failed",
            title="Media Processing Failed",
            body="Background removal could not be completed.",
            payload={
                "processing_job_id": job_id,
                "source_media_id": source_media_id,
                "operation": "background_removal",
            },
            event="aos_media_processing_failed",
            dedupe_key=f"media:processing:{job_id}:failed",
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
                dedupe_key=f"short:new:{short_id}:{user}",
            )

        return None

    @classmethod
    def notify_short_approved(cls, *, user: str, short_id: str, decision_token: str):
        return cls.notify(
            user=user, type="short_approved", title="Short Published",
            body="Your Short was approved and is now visible.",
            payload={"short_id": short_id}, event="aos_short_approved",
            dedupe_key=f"short:approved:{short_id}:{decision_token}",
        )

    @classmethod
    def notify_short_moderation_action(
        cls, *, user: str, short_id: str, action: str, reason: str | None, decision_token: str
    ):
        clean_action = str(action or "updated").strip().lower()
        clean_reason = " ".join(str(reason or "").replace("\x00", "").split())[:500]
        title = "Short Hidden" if clean_action == "hide" else "Short Needs Changes"
        body = "Your Short was hidden." if clean_action == "hide" else "Your Short was not approved."
        if clean_reason:
            body = f"{body} Reason: {clean_reason}"
        return cls.notify(
            user=user, type="short_moderation_action", title=title, body=body,
            payload={"short_id": short_id, "action": clean_action, "reason": clean_reason},
            event="aos_short_moderation_action",
            dedupe_key=f"short:moderation:{short_id}:{clean_action}:{decision_token}",
        )

    @classmethod
    def notify_short_like(
        cls,
        *,
        user: str,
        actor: str,
        short_id: str,
        event_identity: str | None = None,
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
            dedupe_key=(f"short:like:{event_identity}:{user}" if event_identity else None),
        )

    @classmethod
    def notify_short_comment(
        cls,
        *,
        user: str,
        actor: str,
        short_id: str,
        content: str | None = None,
        event_identity: str | None = None,
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
            dedupe_key=(f"short:comment:{event_identity}:{user}" if event_identity else None),
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
        event_identity: str | None = None,
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
            dedupe_key=(f"short:mention:{event_identity}:{user}" if event_identity else None),
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
        event_identity: str | None = None,
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
            dedupe_key=(f"short:reply:{event_identity}:{user}" if event_identity else None),
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
