from __future__ import annotations

import unittest

from aos.services.chat.endpoints import ENDPOINT_SPECS
from aos.services.chat.errors import ChatError
from aos.services.chat.validation import MAX_ATTACHMENTS, validate_public_kwargs


class TestChatPublicValidation(unittest.TestCase):
    def test_unknown_fields_fail_closed(self):
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {"conversation_id": "CONV-2026-00001", "sender": "forged"},
                ENDPOINT_SPECS["send_message"],
            )
        self.assertEqual(raised.exception.code, "CHAT_UNKNOWN_FIELD")

    def test_frappe_cmd_is_the_only_transport_field_stripped(self):
        clean = validate_public_kwargs(
            {"cmd": "aos.api.v1.chat.send_message", "conversation_id": "CONV-2026-00001", "content": "Hello"},
            ENDPOINT_SPECS["send_message"],
        )
        self.assertNotIn("cmd", clean)
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {"conversation_id": "CONV-2026-00001", "role": "moderator"},
                ENDPOINT_SPECS["send_message"],
            )
        self.assertEqual(raised.exception.code, "CHAT_UNKNOWN_FIELD")

    def test_conflicting_aliases_are_rejected(self):
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {"message_id": "MSG-2026-00001", "message_ids": ["MSG-2026-00002"]},
                ENDPOINT_SPECS["delete_messages"],
            )
        self.assertEqual(raised.exception.code, "CHAT_ALIAS_CONFLICT")

    def test_canonical_public_identifiers_are_enforced(self):
        clean = validate_public_kwargs(
            {
                "conversation_id": "CONV-2026-00001",
                "reply_to_message": "MSG-2026-00002",
                "live": "LIVE-2026-00003",
            },
            ENDPOINT_SPECS["send_message"],
        )
        self.assertEqual(clean["live"], "LIVE-2026-00003")
        for field, value in (
            ("conversation_id", "conversation-1"),
            ("reply_to_message", "message-1"),
            ("live", "live-1"),
            ("short", "short-1"),
            ("ad", "ad-1"),
        ):
            with self.subTest(field=field), self.assertRaises(ChatError) as raised:
                validate_public_kwargs({field: value}, ENDPOINT_SPECS["send_message"])
            self.assertEqual(raised.exception.code, "CHAT_INVALID_IDENTIFIER")

    def test_attachments_are_strict_bounded_and_canonical(self):
        clean = validate_public_kwargs(
            {
                "conversation_id": "CONV-2026-00001",
                "attachments": [{"media_id": "MEDIA-2026-00001", "file_type": "image"}],
            },
            ENDPOINT_SPECS["send_message"],
        )
        self.assertEqual(len(clean["attachments"]), 1)
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {
                    "conversation_id": "CONV-2026-00001",
                    "attachments": [{"media_id": "MEDIA-2026-00001", "owner": "forged"}],
                },
                ENDPOINT_SPECS["send_message"],
            )
        self.assertEqual(raised.exception.code, "CHAT_UNKNOWN_FIELD")
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {
                    "conversation_id": "CONV-2026-00001",
                    "attachments": [{"media": "MEDIA-2026-00001", "media_id": "MEDIA-2026-00002"}],
                },
                ENDPOINT_SPECS["send_message"],
            )
        self.assertEqual(raised.exception.code, "CHAT_ALIAS_CONFLICT")
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {
                    "conversation_id": "CONV-2026-00001",
                    "attachments": [{"media_id": f"MEDIA-2026-{index:05d}"} for index in range(MAX_ATTACHMENTS + 1)],
                },
                ENDPOINT_SPECS["send_message"],
            )
        self.assertEqual(raised.exception.code, "CHAT_INPUT_TOO_LARGE")

    def test_text_is_unicode_normalized_trimmed_and_bounded(self):
        clean = validate_public_kwargs(
            {"conversation_id": "CONV-2026-00001", "content": "  Cafe\u0301  "},
            ENDPOINT_SPECS["send_message"],
        )
        self.assertEqual(clean["content"], "Café")
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {"conversation_id": "CONV-2026-00001", "content": "x" * 4001},
                ENDPOINT_SPECS["send_message"],
            )
        self.assertEqual(raised.exception.code, "CHAT_INPUT_TOO_LARGE")
        with self.assertRaises(ChatError):
            validate_public_kwargs(
                {"conversation_id": "CONV-2026-00001", "content": "bad\x00text"},
                ENDPOINT_SPECS["send_message"],
            )

    def test_message_and_forward_lists_are_bounded(self):
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {"message_ids": [f"MSG-2026-{index:05d}" for index in range(101)]},
                ENDPOINT_SPECS["delete_messages"],
            )
        self.assertEqual(raised.exception.code, "CHAT_INPUT_TOO_LARGE")
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {"message_id": "MSG-2026-00001", "target_conversation_ids": [f"CONV-2026-{index:05d}" for index in range(21)]},
                ENDPOINT_SPECS["forward_message"],
            )
        self.assertEqual(raised.exception.code, "CHAT_INPUT_TOO_LARGE")

    def test_pagination_delete_scope_and_boolean_values_are_strict(self):
        clean = validate_public_kwargs(
            {"limit": "50", "offset": "0"},
            ENDPOINT_SPECS["list_conversations"],
        )
        self.assertEqual(clean, {"limit": 50, "offset": 0})
        for value in (0, 101, True, "not-an-int"):
            with self.subTest(value=value), self.assertRaises(ChatError):
                validate_public_kwargs({"limit": value}, ENDPOINT_SPECS["list_conversations"])
        with self.assertRaises(ChatError):
            validate_public_kwargs(
                {"message_id": "MSG-2026-00001", "delete_scope": "admin"},
                ENDPOINT_SPECS["delete_messages"],
            )
        self.assertEqual(
            validate_public_kwargs(
                {"conversation_id": "CONV-2026-00001", "is_typing": "true"},
                ENDPOINT_SPECS["send_typing_event"],
            )["is_typing"],
            1,
        )
        self.assertEqual(
            validate_public_kwargs(
                {"conversation_id": "CONV-2026-00001"},
                ENDPOINT_SPECS["get_presence"],
            ),
            {"conversation_id": "CONV-2026-00001"},
        )

    def test_idempotency_key_is_bounded(self):
        clean = validate_public_kwargs(
            {"conversation_id": "CONV-2026-00001", "content": "Hi", "idempotency_key": "client-send-1"},
            ENDPOINT_SPECS["send_message"],
        )
        self.assertEqual(clean["idempotency_key"], "client-send-1")
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {"conversation_id": "CONV-2026-00001", "content": "Hi", "idempotency_key": "x" * 129},
                ENDPOINT_SPECS["send_message"],
            )
        self.assertEqual(raised.exception.code, "CHAT_INPUT_TOO_LARGE")


if __name__ == "__main__":
    unittest.main()
