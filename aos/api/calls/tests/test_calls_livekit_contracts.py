from __future__ import annotations

import unittest
from unittest.mock import patch

from aos.services.calls.livekit import provision_call_room
from aos.services.livekit.admin import RoomAdminResult
from aos.services.livekit_service import LiveKitService


class TestCallsLiveKitContracts(unittest.TestCase):
    def test_audio_token_is_microphone_only_and_cannot_publish_data_or_mutate_metadata(self):
        with patch.object(LiveKitService, "_generate_token", return_value="token") as generate:
            token = LiveKitService.generate_call_token(
                user="ACC-TEST",
                room_name="call:test",
                call_type="audio",
            )
        self.assertEqual(token, "token")
        kwargs = generate.call_args.kwargs
        self.assertEqual(kwargs["can_publish_sources"], ["microphone"])
        self.assertFalse(kwargs["can_publish_data"])
        self.assertFalse(kwargs["can_update_own_metadata"])
        self.assertTrue(kwargs["can_publish"])
        self.assertTrue(kwargs["can_subscribe"])

    def test_video_token_allows_only_microphone_and_camera(self):
        with patch.object(LiveKitService, "_generate_token", return_value="token") as generate:
            LiveKitService.generate_call_token(
                user="ACC-TEST",
                room_name="call:test",
                call_type="video",
            )
        self.assertEqual(
            generate.call_args.kwargs["can_publish_sources"],
            ["microphone", "camera"],
        )

    def test_invalid_server_call_type_is_rejected_before_token_signing(self):
        with patch.object(LiveKitService, "_generate_token", return_value="token") as generate:
            with self.assertRaises(Exception):
                LiveKitService.generate_call_token(
                    user="ACC-TEST",
                    room_name="call:test",
                    call_type="screen_share",
                )
        generate.assert_not_called()

    def test_live_grants_keep_existing_shared_live_behavior(self):
        with (
            patch.object(LiveKitService, "normalize_live_role", return_value="host"),
            patch.object(LiveKitService, "_generate_token", return_value="live-token") as generate,
        ):
            token = LiveKitService.generate_live_token(
                user="ACC-HOST",
                room_name="live:test",
                role="host",
            )
        self.assertEqual(token, "live-token")
        kwargs = generate.call_args.kwargs
        self.assertIsNone(kwargs["can_publish_sources"])
        self.assertIsNone(kwargs["can_update_own_metadata"])
        self.assertTrue(kwargs["can_publish_data"])

    def test_calls_request_two_participant_room_from_shared_admin_service(self):
        with patch(
            "aos.services.calls.livekit.ensure_room",
            return_value=RoomAdminResult(True, "created"),
        ) as ensure:
            result = provision_call_room("call:opaque")
        self.assertTrue(result.ok)
        ensure.assert_called_once_with("call:opaque", max_participants=2)


if __name__ == "__main__":
    unittest.main()
