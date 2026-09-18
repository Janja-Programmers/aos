from __future__ import annotations

import unittest

from aos.api.shared.transport import client_kwargs
from aos.services.live.endpoints import ENDPOINT_SPECS
from aos.services.live.errors import LiveError
from aos.services.live.validation import validate_public_kwargs


class TestLivePublicValidation(unittest.TestCase):
    def test_unknown_fields_fail_closed(self):
        with self.assertRaises(LiveError) as raised:
            validate_public_kwargs(
                {"live_id": "LIVE-2026-00001", "role": "host"},
                ENDPOINT_SPECS["join_live"],
            )
        self.assertEqual(raised.exception.code, "LIVE_UNKNOWN_FIELD")

    def test_transport_cmd_is_accepted_only_at_public_boundary(self):
        payload = client_kwargs(
            {
                "cmd": "aos.api.v1.live.get_live",
                "live_id": "LIVE-2026-00001",
            }
        )
        self.assertEqual(payload, {"live_id": "LIVE-2026-00001"})
        with self.assertRaises(LiveError) as raised:
            validate_public_kwargs(
                {"live_id": "LIVE-2026-00001", "owner": "forged"},
                ENDPOINT_SPECS["get_live"],
            )
        self.assertEqual(raised.exception.code, "LIVE_UNKNOWN_FIELD")

    def test_transport_filter_does_not_strip_unknown_client_fields(self):
        payload = client_kwargs({"cmd": "route", "role": "host", "viewer_count": 999})
        self.assertEqual(payload, {"role": "host", "viewer_count": 999})

    def test_structured_values_are_rejected_for_scalar_fields(self):
        for endpoint, field, value in (
            ("start_live", "title", {"text": "forged"}),
            ("start_live", "cover_image", ["https://invalid.test/cover"]),
            ("add_live_message", "content", {"html": "<script>"}),
            ("send_reaction", "reaction_type", ["heart"]),
            ("respond_live_cohost", "action", {"value": "accept"}),
        ):
            with self.subTest(endpoint=endpoint, field=field), self.assertRaises(LiveError):
                validate_public_kwargs({field: value}, ENDPOINT_SPECS[endpoint])

    def test_conflicting_cover_aliases_are_rejected(self):
        with self.assertRaises(LiveError) as raised:
            validate_public_kwargs(
                {"title": "Launch", "live_cover_media": "MEDIA-00000000000000000000000000000001", "media_id": "MEDIA-00000000000000000000000000000002"},
                ENDPOINT_SPECS["start_live"],
            )
        self.assertEqual(raised.exception.code, "LIVE_ALIAS_CONFLICT")

    def test_matching_cover_aliases_remain_backward_compatible(self):
        clean = validate_public_kwargs(
            {"title": "Launch", "live_cover_media": "MEDIA-00000000000000000000000000000001", "media_id": "MEDIA-00000000000000000000000000000001"},
            ENDPOINT_SPECS["start_live"],
        )
        self.assertEqual(clean["live_cover_media"], "MEDIA-00000000000000000000000000000001")

    def test_live_identifier_is_canonical(self):
        clean = validate_public_kwargs(
            {"live_id": "LIVE-2026-00001", "session_id": "session-1"},
            ENDPOINT_SPECS["join_live"],
        )
        self.assertEqual(clean["live_id"], "LIVE-2026-00001")
        with self.assertRaises(LiveError) as raised:
            validate_public_kwargs(
                {"live_id": "live-1", "session_id": "session-1"},
                ENDPOINT_SPECS["join_live"],
            )
        self.assertEqual(raised.exception.code, "LIVE_INVALID_IDENTIFIER")

    def test_cursor_and_offset_conflict_is_rejected(self):
        with self.assertRaises(LiveError) as raised:
            validate_public_kwargs(
                {"cursor": "opaque", "start": 10},
                ENDPOINT_SPECS["list_live_streams"],
            )
        self.assertEqual(raised.exception.code, "LIVE_PAGINATION_CONFLICT")

    def test_pagination_is_strict_and_bounded(self):
        clean = validate_public_kwargs(
            {"limit": "50", "start": "0"},
            ENDPOINT_SPECS["list_live_streams"],
        )
        self.assertEqual(clean, {"limit": 50, "start": 0})
        for value in (0, 101, True, "not-an-int"):
            with self.subTest(value=value), self.assertRaises(LiveError):
                validate_public_kwargs(
                    {"limit": value},
                    ENDPOINT_SPECS["list_live_streams"],
                )

    def test_text_limits_and_nul_rejection(self):
        with self.assertRaises(LiveError) as too_long:
            validate_public_kwargs(
                {"title": "x" * 141},
                ENDPOINT_SPECS["start_live"],
            )
        self.assertEqual(too_long.exception.code, "LIVE_INPUT_TOO_LARGE")
        with self.assertRaises(LiveError):
            validate_public_kwargs(
                {"title": "bad\x00title"},
                ENDPOINT_SPECS["start_live"],
            )

    def test_host_cohost_invite_accepts_opaque_participant_identity(self):
        payload = validate_public_kwargs(
            {
                "live_id": "LIVE-2026-00001",
                "livekit_identity": "aos:participant:abcdefghijklmnopqrstuvwx",
            },
            ENDPOINT_SPECS["invite_live_cohost"],
        )
        self.assertEqual(
            payload["livekit_identity"],
            "aos:participant:abcdefghijklmnopqrstuvwx",
        )

    def test_host_cohost_invite_rejects_non_participant_identity(self):
        with self.assertRaises(LiveError) as raised:
            validate_public_kwargs(
                {
                    "live_id": "LIVE-2026-00001",
                    "livekit_identity": "aos:host:abcdefghijklmnopqrstuvwx",
                },
                ENDPOINT_SPECS["invite_live_cohost"],
            )
        self.assertEqual(raised.exception.code, "LIVE_INVALID_IDENTIFIER")


if __name__ == "__main__":
    unittest.main()
