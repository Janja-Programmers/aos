from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


class TestCallsLiveKitContracts(unittest.TestCase):
    def test_calls_room_capacity_is_server_owned_and_mode_bounded(self):
        source = (ROOT / "aos/services/calls/livekit.py").read_text()
        self.assertIn("DIRECT_CALL_MAX_PARTICIPANTS = 2", source)
        self.assertIn("GROUP_CALL_MAX_PARTICIPANTS = 32", source)
        self.assertIn("max_participants=bounded", source)
        self.assertIn("CALL_ROOM_MAX_PARTICIPANTS = GROUP_CALL_MAX_PARTICIPANTS", source)
        self.assertIn("def room_capacity_for_call", source)
        self.assertIn("return CALL_ROOM_MAX_PARTICIPANTS", source)
        self.assertIn("bounded not in {DIRECT_CALL_MAX_PARTICIPANTS, GROUP_CALL_MAX_PARTICIPANTS}", source)

    def test_call_media_grants_remain_least_privilege(self):
        source = (ROOT / "aos/services/livekit_service.py").read_text()
        call_block = source.split("def generate_call_token", 1)[1].split("def generate_live_token", 1)[0]
        self.assertIn('["microphone"]', call_block)
        self.assertIn('publish_sources = ["microphone"]', call_block)
        self.assertIn('publish_sources.append("camera")', call_block)
        self.assertIn("can_publish_data=False", call_block)
        self.assertIn("can_update_own_metadata=False", call_block)

    def test_live_grants_remain_separate(self):
        source = (ROOT / "aos/services/livekit_service.py").read_text()
        live_block = source.split("def generate_live_token", 1)[1]
        self.assertIn("LIVE_ROLE_GRANTS", live_block)
        self.assertIn("can_publish_data", live_block)


if __name__ == "__main__":
    unittest.main()
