from __future__ import annotations

import frappe

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except ImportError:
    firebase_admin = None


class PushService:
    """
    Firebase Cloud Messaging (FCM) push service.

    Responsibilities:
    - Initialize Firebase Admin SDK
    - Fetch active tokens for user
    - Send push notifications
    """

    _initialized = False

    # INIT
    @classmethod
    def _init(cls):
        """
        Initialize Firebase Admin SDK (lazy).
        """
        if cls._initialized:
            return

        if not firebase_admin:
            frappe.log_error("firebase_admin not installed", "PushService Init Failed")
            return

        try:
            if not firebase_admin._apps:
                cred_path = frappe.conf.get("firebase_service_account")

                if not cred_path:
                    frappe.log_error(
                        "Missing firebase_service_account in site config",
                        "PushService Init Failed",
                    )
                    return

                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred)

            cls._initialized = True

        except Exception:
            frappe.log_error(frappe.get_traceback(), "PushService Init Error")

    # TOKEN FETCH
    @staticmethod
    def _get_active_tokens(user: str) -> list[str]:
        """
        Fetch active tokens for a user.
        """
        if not user:
            return []

        return frappe.get_all(
            "AOS Push Token",
            filters={"user": user, "is_active": 1},
            pluck="token",
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
    ):
        """
        Send push notification to all active devices of a user.
        """

        if not user:
            return

        cls._init()

        if not firebase_admin or not cls._initialized:
            return

        tokens = cls._get_active_tokens(user)

        if not tokens:
            return

        # FCM requires string values in data payload
        data_payload = {
            str(k): str(v) for k, v in (data or {}).items()
        }

        try:
            message = messaging.MulticastMessage(
                notification=messaging.Notification(
                    title=title,
                    body=body,
                ),
                data=data_payload,
                tokens=tokens,
            )

            response = messaging.send_multicast(message)

            # Handle invalid tokens
            if response.failure_count:
                cls._handle_failures(tokens, response)

        except Exception:
            frappe.log_error(frappe.get_traceback(), "PushService Send Error")

    # CLEANUP
    @staticmethod
    def _handle_failures(tokens: list[str], response):
        """
        Deactivate invalid tokens.
        """
        for idx, resp in enumerate(response.responses):
            if resp.success:
                continue

            error = str(resp.exception)

            # Common invalid token errors
            if any(
                err in error
                for err in [
                    "registration-token-not-registered",
                    "invalid-registration-token",
                ]
            ):
                token = tokens[idx]

                frappe.db.set_value(
                    "AOS Push Token",
                    {"token": token},
                    "is_active",
                    0,
                    update_modified=False,
                )
