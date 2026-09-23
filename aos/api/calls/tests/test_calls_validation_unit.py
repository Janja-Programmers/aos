from __future__ import annotations

import unittest

from aos.services.calls.endpoints import ENDPOINT_SPECS
from aos.services.calls.errors import CallError
from aos.services.calls.validation import MAX_CALL_IDS, validate_public_kwargs

CALL_A = "call_" + ("a" * 32)
CALL_B = "call_" + ("b" * 32)


class TestCallsPublicValidation(unittest.TestCase):
    def test_unknown_fields_fail_closed_and_cmd_is_stripped(self):
        clean = validate_public_kwargs(
            {"cmd": "aos.api.v1.calls.initiate_call", "conversation_id": "CONV-2026-00001", "call_type": "audio"},
            ENDPOINT_SPECS["initiate_call"],
        )
        self.assertEqual(clean, {"conversation_id": "CONV-2026-00001", "call_type": "audio"})
        with self.assertRaises(CallError) as raised:
            validate_public_kwargs(
                {"conversation_id": "CONV-2026-00001", "caller": "forged@example.com"},
                ENDPOINT_SPECS["initiate_call"],
            )
        self.assertEqual(raised.exception.code, "CALL_UNKNOWN_FIELD")

    def test_canonical_public_call_and_conversation_ids_are_required(self):
        valid = validate_public_kwargs(
            {"conversation_id": "CONV-2026-00001"},
            ENDPOINT_SPECS["initiate_call"],
        )
        self.assertEqual(valid["conversation_id"], "CONV-2026-00001")
        for endpoint, field, value in (
            ("initiate_call", "conversation_id", "conversation-1"),
            ("accept_call", "call_id", "call-1"),
            ("get_call_group_details", "latest_call_id", "CALL-1"),
            ("list_calls", "cursor_call_id", "CALL-2026-1"),
        ):
            with self.subTest(endpoint=endpoint, field=field), self.assertRaises(CallError) as raised:
                validate_public_kwargs({field: value}, ENDPOINT_SPECS[endpoint])
            self.assertEqual(raised.exception.code, "CALL_INVALID_IDENTIFIER")

    def test_legacy_status_alias_is_rejected(self):
        clean = validate_public_kwargs(
            {"call_id": CALL_A},
            ENDPOINT_SPECS["get_call_status"],
        )
        self.assertEqual(clean["call_id"], CALL_A)
        with self.assertRaises(CallError) as raised:
            validate_public_kwargs(
                {"call_id": CALL_A, "id": CALL_A},
                ENDPOINT_SPECS["get_call_status"],
            )
        self.assertEqual(raised.exception.code, "CALL_UNKNOWN_FIELD")

    def test_history_cursor_and_limit_are_strict(self):
        clean = validate_public_kwargs(
            {"limit": "50", "cursor_created_at": "2026-08-10T10:30:00+03:00", "cursor_call_id": CALL_A},
            ENDPOINT_SPECS["list_calls"],
        )
        self.assertEqual(clean["limit"], 50)
        for value in (0, 101, True, "bad"):
            with self.subTest(limit=value), self.assertRaises(CallError):
                validate_public_kwargs({"limit": value}, ENDPOINT_SPECS["list_calls"])
        with self.assertRaises(CallError) as raised:
            validate_public_kwargs({"cursor_created_at": "not-a-date"}, ENDPOINT_SPECS["list_calls"])
        self.assertEqual(raised.exception.code, "CALL_INVALID_CURSOR")

    def test_call_id_lists_are_canonical_deduplicated_and_bounded(self):
        clean = validate_public_kwargs(
            {"call_ids": f'["{CALL_A}","{CALL_A}","{CALL_B}"]'},
            ENDPOINT_SPECS["delete_call_logs"],
        )
        self.assertEqual(clean["call_ids"], [CALL_A, CALL_B])
        with self.assertRaises(CallError) as raised:
            validate_public_kwargs({"call_ids": ["internal-name"]}, ENDPOINT_SPECS["delete_call_logs"])
        self.assertEqual(raised.exception.code, "CALL_INVALID_IDENTIFIER")
        with self.assertRaises(CallError) as raised:
            validate_public_kwargs(
                {"call_ids": ["call_" + f"{index:032x}" for index in range(MAX_CALL_IDS + 1)]},
                ENDPOINT_SPECS["delete_call_logs"],
            )
        self.assertEqual(raised.exception.code, "CALL_INPUT_TOO_LARGE")

    def test_text_fields_are_unicode_normalized_trimmed_and_bounded(self):
        clean = validate_public_kwargs(
            {"conversation_id": "CONV-2026-00001", "call_type": "  AUDIO  "},
            ENDPOINT_SPECS["initiate_call"],
        )
        self.assertEqual(clean["call_type"], "AUDIO")
        with self.assertRaises(CallError):
            validate_public_kwargs(
                {"conversation_id": "CONV-2026-00001", "call_type": "bad\x00value"},
                ENDPOINT_SPECS["initiate_call"],
            )

    def test_every_public_endpoint_has_a_strict_spec(self):
        self.assertEqual(
            set(ENDPOINT_SPECS),
            {
                "initiate_call", "mark_call_ringing", "accept_call", "reject_call", "cancel_call",
                "end_call", "request_video_upgrade", "respond_video_upgrade", "get_call_status",
                "get_call_token", "list_calls", "get_call_group_details", "delete_call_logs",
                "clear_call_history",
            },
        )


if __name__ == "__main__":
    unittest.main()
