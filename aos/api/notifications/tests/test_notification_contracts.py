from __future__ import annotations

import unittest

from aos.services.notifications.contracts import (
    CATEGORY_TYPES,
    NotificationContractError,
    canonical_event,
    sanitize_public_payload,
    validate_persistent_payload,
)
from aos.services.notifications.devices import (
    PushDeviceValidationError,
    get_token_hash,
    normalize_device_id,
    normalize_device_type,
    normalize_push_token,
    normalize_registration_kind,
    token_fingerprint,
)


class TestNotificationContracts(unittest.TestCase):
    def test_authoritative_categories_include_existing_short_mention_producer(self):
        self.assertEqual(
            set(CATEGORY_TYPES["communication"]),
            {"message", "missed_call"},
        )
        self.assertEqual(
            set(CATEGORY_TYPES["activity"]),
            {
                "follow",
                "new_short",
                "short_like",
                "short_comment",
                "short_mention",
                "comment_reply",
                "live_started",
                "media_processing_completed",
                "media_processing_failed",
            },
        )
        self.assertEqual(
            set(CATEGORY_TYPES["marketplace"]),
            {
                "ad_approved",
                "ad_rejected",
                "ad_expired",
                "seller_status_changed",
                "review_received",
                "review_approved",
                "review_rejected",
            },
        )
        self.assertEqual(
            set(CATEGORY_TYPES["account"]),
            {"verification_approved", "verification_rejected"},
        )
        self.assertEqual(canonical_event("short_mention"), "aos_short_mention")

    def test_payloads_are_typed_strict_scalar_and_bounded(self):
        valid = validate_persistent_payload(
            "message",
            {
                "conversation_id": "CONV-1",
                "sender_account_id": "ACC-1",
                "message_id": "MSG-1",
            },
        )
        self.assertEqual(valid["conversation_id"], "CONV-1")

        with self.assertRaises(NotificationContractError):
            validate_persistent_payload(
                "message",
                {
                    "conversation_id": "CONV-1",
                    "sender_account_id": "ACC-1",
                    "unexpected": "private",
                },
            )
        with self.assertRaises(NotificationContractError):
            validate_persistent_payload(
                "message",
                {"conversation_id": "CONV-1", "sender_account_id": {"raw": "ACC-1"}},
            )
        with self.assertRaises(NotificationContractError):
            validate_persistent_payload("message", {"conversation_id": "CONV-1"})

    def test_historical_public_payload_sanitizer_fails_closed(self):
        safe = sanitize_public_payload(
            "short_comment",
            {
                "short_id": "SHORT-1",
                "actor": "ACC-1",
                "content": "hello",
                "internal_user": "private@example.com",
                "provider_token": "secret",
            },
        )
        self.assertEqual(
            safe,
            {"short_id": "SHORT-1", "actor": "ACC-1", "content": "hello"},
        )
        self.assertEqual(sanitize_public_payload("unknown", {"x": "y"}), {})

    def test_push_device_validation_and_diagnostics_never_need_raw_token(self):
        token = "fcm_test_token_abcdefghijklmnopqrstuvwxyz_0123456789"
        normalized = normalize_push_token(token)
        digest = get_token_hash(normalized)
        self.assertEqual(len(digest), 64)
        self.assertEqual(token_fingerprint(token=normalized), digest[:12])
        self.assertEqual(normalize_device_type("ANDROID"), "android")
        self.assertEqual(normalize_device_id("device-01"), "device-01")
        with self.assertRaises(PushDeviceValidationError):
            normalize_registration_kind(None)
        self.assertEqual(normalize_registration_kind("FID"), "fid")

        for invalid in ("short", "token with spaces and enough length"):
            with self.assertRaises(PushDeviceValidationError):
                normalize_push_token(invalid)
        with self.assertRaises(PushDeviceValidationError):
            normalize_device_type("desktop")
        with self.assertRaises(PushDeviceValidationError):
            normalize_registration_kind("topic")
        with self.assertRaises(PushDeviceValidationError):
            normalize_device_id("unsafe/device")
        with self.assertRaises(PushDeviceValidationError):
            normalize_device_id(None)


if __name__ == "__main__":
    unittest.main()
