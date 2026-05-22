from __future__ import annotations

import datetime
import hashlib
import frappe
from frappe.utils import now_datetime

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except ImportError:
    firebase_admin = None
    credentials = None
    messaging = None


def get_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def chunk_records(records: list[dict], size: int = 500):
    """
    Chunk list of token records.
    Each record contains: {token, token_hash}
    """
    for i in range(0, len(records), size):
        yield records[i : i + size]


class PushService:
    """
    Firebase Cloud Messaging push service.
    """

    _initialized = False

    # INIT
    @classmethod
    def _init(cls) -> bool:
        if cls._initialized:
            return True

        if not firebase_admin:
            frappe.log_error(
                "firebase_admin not installed",
                "PushService Init Failed",
            )
            return False

        try:
            if not firebase_admin._apps:
                cred_path = frappe.conf.get("firebase_service_account")

                if not cred_path:
                    frappe.log_error(
                        "Missing firebase_service_account in site config",
                        "PushService Init Failed",
                    )
                    return False

                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred)

            cls._initialized = True
            return True

        except Exception as e:
            frappe.log_error(
                frappe.get_traceback(),
                f"PushService Init Error: {e}",
            )
            return False

    # TOKEN FETCH
    @staticmethod
    def _get_active_tokens(user: str) -> list[dict]:
        if not user:
            return []

        return frappe.get_all(
            "AOS Push Token",
            filters={
                "user": user,
                "is_active": 1,
            },
            fields=[
                "token",
                "token_hash",
            ],
        )

    # PAYLOAD HELPERS
    @staticmethod
    def _stringify_data(data: dict | None) -> dict[str, str]:
        """
        Firebase data payload values must be strings.

        Keep this strict because Flutter's FCM handlers expect custom call data
        in message.data.
        """
        return {
            str(k): str(v)
            for k, v in (data or {}).items()
            if v is not None
        }

    @staticmethod
    def _normalize_android_priority(priority: str | None) -> str | None:
        """
        Firebase Admin SDK AndroidConfig.priority expects "high" or "normal".
        """
        if not priority:
            return None

        normalized = str(priority).strip().lower()

        if normalized in {"high", "normal"}:
            return normalized

        return None

    @staticmethod
    def _normalize_android_notification_priority(
        android_notification_priority: str | None,
    ) -> str | None:
        """
        Firebase Admin SDK AndroidNotification.priority commonly accepts:
        min, low, default, high, max.

        For incoming calls, NotificationService passes "max".
        """
        if not android_notification_priority:
            return None

        normalized = str(android_notification_priority).strip().lower()

        if normalized in {"min", "low", "default", "high", "max"}:
            return normalized

        return None

    @classmethod
    def _build_android_config(
        cls,
        *,
        priority: str | None = None,
        ttl_seconds: int | None = None,
        android_channel_id: str | None = None,
        android_notification_priority: str | None = None,
    ):
        """
        Build optional Android-specific FCM config.

        Used by incoming calls to request urgent delivery and avoid stale call
        notifications being delivered too late.
        """
        if not any(
            [
                priority,
                ttl_seconds,
                android_channel_id,
                android_notification_priority,
            ]
        ):
            return None

        normalized_priority = cls._normalize_android_priority(priority)
        normalized_notification_priority = (
            cls._normalize_android_notification_priority(
                android_notification_priority
            )
        )

        ttl = None
        if ttl_seconds is not None:
            try:
                ttl_int = max(0, int(ttl_seconds))
                ttl = datetime.timedelta(seconds=ttl_int)
            except Exception:
                ttl = None

        android_notification = None
        if android_channel_id or normalized_notification_priority:
            android_notification = messaging.AndroidNotification(
                channel_id=android_channel_id,
                priority=normalized_notification_priority,
            )

        return messaging.AndroidConfig(
            priority=normalized_priority,
            ttl=ttl,
            notification=android_notification,
        )

    # SEND
    @classmethod
    def send_to_user(
        cls,
        *,
        user: str,
        title: str,
        body: str,
        data: dict | None = None,
        priority: str | None = None,
        ttl_seconds: int | None = None,
        android_channel_id: str | None = None,
        android_notification_priority: str | None = None,
    ):
        """
        Send push notification to all active devices for a user.

        Optional Android config parameters are backward-compatible. Existing
        callers can continue passing only user/title/body/data.
        """
        if not user:
            return

        if not cls._init():
            return

        token_records = cls._get_active_tokens(user)

        if not token_records:
            return

        # Deduplicate using token_hash.
        unique = {
            r["token_hash"]: r
            for r in token_records
            if r.get("token") and r.get("token_hash")
        }

        records = list(unique.values())

        if not records:
            return

        data_payload = cls._stringify_data(data)

        android_config = cls._build_android_config(
            priority=priority,
            ttl_seconds=ttl_seconds,
            android_channel_id=android_channel_id,
            android_notification_priority=android_notification_priority,
        )

        try:
            for chunk in chunk_records(records, 500):
                tokens = [r["token"] for r in chunk if r.get("token")]

                if not tokens:
                    continue

                message = messaging.MulticastMessage(
                    notification=messaging.Notification(
                        title=title,
                        body=body,
                    ),
                    data=data_payload,
                    tokens=tokens,
                    android=android_config,
                )

                response = messaging.send_each_for_multicast(message)

                # Handle invalid/dead tokens.
                if response.failure_count:
                    cls._handle_failures(chunk, response)

                # Update successful token usage.
                if response.success_count:
                    cls._update_last_used(chunk, response)

        except Exception as e:
            frappe.log_error(
                frappe.get_traceback(),
                f"PushService Send Error: {e}",
            )

    # CLEANUP FAILURES
    @staticmethod
    def _handle_failures(chunk: list[dict], response):
        for idx, resp in enumerate(response.responses):
            if resp.success:
                continue

            error = str(resp.exception or "")

            if any(
                err in error
                for err in [
                    "registration-token-not-registered",
                    "invalid-registration-token",
                    "Requested entity was not found",
                    "The registration token is not a valid FCM registration token",
                ]
            ):
                record = chunk[idx]

                frappe.db.set_value(
                    "AOS Push Token",
                    {
                        "token_hash": record["token_hash"],
                    },
                    "is_active",
                    0,
                    update_modified=False,
                )

    # UPDATE LAST USED
    @staticmethod
    def _update_last_used(chunk: list[dict], response):
        now = now_datetime()

        for idx, resp in enumerate(response.responses):
            if not resp.success:
                continue

            record = chunk[idx]

            frappe.db.set_value(
                "AOS Push Token",
                {
                    "token_hash": record["token_hash"],
                },
                "last_used_at",
                now,
                update_modified=False,
            )
