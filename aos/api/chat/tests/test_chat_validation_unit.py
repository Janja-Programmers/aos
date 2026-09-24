from __future__ import annotations

import unittest

from aos.services.chat.cursors import decode_cursor, encode_cursor
from aos.services.chat.endpoints import ENDPOINT_SPECS
from aos.services.chat.errors import ChatError
from aos.services.chat.validation import MAX_ATTACHMENTS, validate_public_kwargs

CONV = "CONV-" + "a" * 32
MSG = "MSG-" + "b" * 32
MSG_2 = "MSG-" + "c" * 32
MEDIA = "MEDIA-" + "d" * 32
SHORT = "SHR-" + "A" * 20
LIVE = "LIVE-" + "e" * 32
AD = "ad_" + "A" * 24


class TestChatPublicValidation(unittest.TestCase):
    def test_unknown_fields_and_removed_aliases_fail_closed(self):
        for endpoint, payload in (
            ("send_message", {"conversation_id": CONV, "sender": "forged"}),
            ("delete_messages", {"message_id": MSG}),
            ("forward_message", {"message_id": MSG, "target_conversation_id": CONV}),
            ("list_conversations", {"offset": 0}),
            ("list_starred_messages", {"before": MSG}),
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ChatError) as raised:
                validate_public_kwargs(payload, ENDPOINT_SPECS[endpoint])
            self.assertEqual(raised.exception.code, "CHAT_UNKNOWN_FIELD")

    def test_frappe_cmd_is_the_only_transport_field_stripped(self):
        clean = validate_public_kwargs(
            {"cmd": "aos.api.v1.chat.send_message", "conversation_id": CONV, "content": "Hello"},
            ENDPOINT_SPECS["send_message"],
        )
        self.assertNotIn("cmd", clean)
        self.assertEqual(clean["conversation_id"], CONV)

    def test_canonical_public_identifiers_are_enforced(self):
        clean = validate_public_kwargs(
            {
                "conversation_id": CONV,
                "reply_to_message": MSG,
                "live": LIVE,
                "short": SHORT,
                "ad": AD,
            },
            ENDPOINT_SPECS["send_message"],
        )
        self.assertEqual(clean["short"], SHORT)
        for field, value in (
            ("conversation_id", "CONV-2026-00001"),
            ("reply_to_message", "MSG-2026-00001"),
            ("live", "live-1"),
            ("short", "SHORT-2026-00001"),
            ("ad", "AD-internal-name"),
        ):
            with self.subTest(field=field), self.assertRaises(ChatError) as raised:
                validate_public_kwargs({field: value}, ENDPOINT_SPECS["send_message"])
            self.assertEqual(raised.exception.code, "CHAT_INVALID_IDENTIFIER")

    def test_attachments_are_media_only_bounded_and_deduplicated(self):
        clean = validate_public_kwargs(
            {"conversation_id": CONV, "attachments": [{"media_id": MEDIA}]},
            ENDPOINT_SPECS["send_message"],
        )
        self.assertEqual(clean["attachments"], [{"media_id": MEDIA}])
        for attachment in (
            {"media_id": MEDIA, "file_type": "image"},
            {"media": MEDIA},
        ):
            with self.assertRaises(ChatError) as raised:
                validate_public_kwargs(
                    {"conversation_id": CONV, "attachments": [attachment]},
                    ENDPOINT_SPECS["send_message"],
                )
            self.assertEqual(raised.exception.code, "CHAT_UNKNOWN_FIELD")
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {"conversation_id": CONV, "attachments": [{"media_id": MEDIA}, {"media_id": MEDIA}]},
                ENDPOINT_SPECS["send_message"],
            )
        self.assertEqual(raised.exception.code, "CHAT_CONFLICT")
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {
                    "conversation_id": CONV,
                    "attachments": [{"media_id": f"MEDIA-{index:032x}"} for index in range(MAX_ATTACHMENTS + 1)],
                },
                ENDPOINT_SPECS["send_message"],
            )
        self.assertEqual(raised.exception.code, "CHAT_INPUT_TOO_LARGE")

    def test_text_is_unicode_normalized_trimmed_and_bounded(self):
        clean = validate_public_kwargs(
            {"conversation_id": CONV, "content": "  Cafe\u0301  "},
            ENDPOINT_SPECS["send_message"],
        )
        self.assertEqual(clean["content"], "Café")
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {"conversation_id": CONV, "content": "x" * 4001},
                ENDPOINT_SPECS["send_message"],
            )
        self.assertEqual(raised.exception.code, "CHAT_INPUT_TOO_LARGE")

    def test_message_and_forward_lists_are_bounded_and_require_lists(self):
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs({"message_ids": MSG}, ENDPOINT_SPECS["delete_messages"])
        self.assertEqual(raised.exception.code, "CHAT_INVALID_REQUEST")
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {"message_ids": [f"MSG-{index:032x}" for index in range(101)]},
                ENDPOINT_SPECS["delete_messages"],
            )
        self.assertEqual(raised.exception.code, "CHAT_INPUT_TOO_LARGE")
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {"message_id": MSG, "target_conversation_ids": [f"CONV-{index:032x}" for index in range(21)]},
                ENDPOINT_SPECS["forward_message"],
            )
        self.assertEqual(raised.exception.code, "CHAT_INPUT_TOO_LARGE")

    def test_cursor_pagination_delete_scope_and_boolean_values_are_strict(self):
        cursor = encode_cursor("conversations", {"activity_at": "2026-09-24 10:00:00", "conversation_id": CONV})
        clean = validate_public_kwargs({"limit": "50", "cursor": cursor}, ENDPOINT_SPECS["list_conversations"])
        self.assertEqual(clean, {"limit": 50, "cursor": cursor})
        decoded = decode_cursor(clean["cursor"], kind="conversations", required_keys=("activity_at", "conversation_id"))
        self.assertEqual(decoded["conversation_id"], CONV)
        with self.assertRaises(ChatError) as raised:
            decode_cursor("not-a-cursor", kind="conversations", required_keys=("activity_at", "conversation_id"))
        self.assertEqual(raised.exception.code, "CHAT_INVALID_CURSOR")
        for value in (0, 101, True, "not-an-int"):
            with self.subTest(value=value), self.assertRaises(ChatError):
                validate_public_kwargs({"limit": value}, ENDPOINT_SPECS["list_conversations"])
        with self.assertRaises(ChatError):
            validate_public_kwargs({"message_ids": [MSG], "delete_scope": "admin"}, ENDPOINT_SPECS["delete_messages"])
        self.assertEqual(
            validate_public_kwargs({"conversation_id": CONV, "is_typing": "true"}, ENDPOINT_SPECS["send_typing_event"])["is_typing"],
            1,
        )
        self.assertEqual(
            validate_public_kwargs({"message_id": MSG_2, "starred": "false"}, ENDPOINT_SPECS["set_message_star"])["starred"],
            0,
        )

    def test_idempotency_key_is_bounded(self):
        clean = validate_public_kwargs(
            {"conversation_id": CONV, "content": "Hi", "idempotency_key": "client-send-1"},
            ENDPOINT_SPECS["send_message"],
        )
        self.assertEqual(clean["idempotency_key"], "client-send-1")
        with self.assertRaises(ChatError) as raised:
            validate_public_kwargs(
                {"conversation_id": CONV, "content": "Hi", "idempotency_key": "x" * 129},
                ENDPOINT_SPECS["send_message"],
            )
        self.assertEqual(raised.exception.code, "CHAT_INPUT_TOO_LARGE")


if __name__ == "__main__":
    unittest.main()
