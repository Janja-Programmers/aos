from __future__ import annotations

import unittest
from unittest.mock import patch

from aos.services.notifications.web_push import WebPushConfigurationError, get_web_push_config


class TestNotificationWebPushConfig(unittest.TestCase):
    def test_disabled_is_safe_default(self):
        with patch(
            "aos.services.notifications.web_push.get_env_bool",
            return_value=False,
        ):
            self.assertEqual(get_web_push_config().public_payload(), {"enabled": False})

    def test_enabled_requires_complete_public_configuration(self):
        # Patch the configuration boundary rather than os.environ. ``get_env``
        # intentionally falls back to the deployment .env for host Bench
        # processes, so environment-only patching is not isolated on staging.
        with (
            patch(
                "aos.services.notifications.web_push.get_env_bool",
                return_value=True,
            ),
            patch(
                "aos.services.notifications.web_push.get_env",
                return_value="",
            ),
        ):
            with self.assertRaises(WebPushConfigurationError):
                get_web_push_config()

    def test_enabled_returns_only_public_firebase_bootstrap(self):
        env = {
            "NOTIFICATION_WEB_PUSH_ENABLED": "true",
            "NOTIFICATION_FIREBASE_WEB_API_KEY": "AOSFirebasePublicApiKey0123456789abcdef",
            "NOTIFICATION_FIREBASE_WEB_AUTH_DOMAIN": "auth.aos.africa",
            "NOTIFICATION_FIREBASE_WEB_PROJECT_ID": "aos-production-2026",
            "NOTIFICATION_FIREBASE_WEB_STORAGE_BUCKET": "aos-production-2026.firebasestorage.app",
            "NOTIFICATION_FIREBASE_WEB_MESSAGING_SENDER_ID": "123456789012",
            "NOTIFICATION_FIREBASE_WEB_APP_ID": "1:123456789012:web:abcdef0123456789",
            "NOTIFICATION_FIREBASE_WEB_MEASUREMENT_ID": "G-AOS2026",
            "NOTIFICATION_FIREBASE_WEB_VAPID_PUBLIC_KEY": "B" + "a" * 86,
            "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH": "/run/secrets/private.json",
            "NOTIFICATION_SERVICE_SECRET": "server-only-secret-value",
        }
        with (
            patch(
                "aos.services.notifications.web_push.get_env_bool",
                return_value=True,
            ),
            patch(
                "aos.services.notifications.web_push.get_env",
                side_effect=lambda name, default=None: env.get(name, default),
            ),
        ):
            payload = get_web_push_config().public_payload()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["firebase"]["projectId"], "aos-production-2026")
        self.assertEqual(payload["vapidPublicKey"], "B" + "a" * 86)
        serialized = repr(payload)
        self.assertNotIn("private.json", serialized)
        self.assertNotIn("server-only-secret-value", serialized)
        self.assertNotIn("serviceAccount", serialized)
        self.assertNotIn("privateKey", serialized)


if __name__ == "__main__":
    unittest.main()
