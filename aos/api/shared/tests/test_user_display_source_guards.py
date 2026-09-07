from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class TestUserDisplaySourceGuards(unittest.TestCase):
    """Protect the v11+ public identity contract across feature consumers."""

    def test_shared_display_exposes_account_id_not_internal_user_alias(self):
        source = _source("aos/api/shared/user_display.py")
        self.assertIn('"account_id": account_id', source)
        self.assertNotIn('"user": account_id', source)

    def test_calls_and_chat_consume_account_id(self):
        calls = _source("aos/api/calls/realtime.py")
        self.assertIn('caller.get("account_id")', calls)
        self.assertIn('receiver.get("account_id")', calls)
        self.assertNotIn('caller.get("user")', calls)
        self.assertNotIn('receiver.get("user")', calls)

        for path in (
            "aos/api/chat/conversation.py",
            "aos/api/chat/message.py",
            "aos/api/chat/presence.py",
        ):
            source = _source(path)
            self.assertNotIn('get("user") if', source, path)
            self.assertNotIn('["user"]', source, path)

    def test_live_consumes_account_id_from_shared_display(self):
        serializers = _source("aos/api/live/serializers.py")
        reactions = _source("aos/api/live/reactions.py")
        realtime = _source("aos/api/live/realtime.py")
        self.assertIn('"user": display["account_id"]', serializers)
        self.assertIn('"host_user": host["account_id"]', serializers)
        self.assertIn('"user": user_payload["account_id"]', reactions)
        self.assertIn('"host_user": host["account_id"]', realtime)

    def test_seller_relationship_fallback_uses_public_account_id(self):
        source = _source("aos/services/sellers/service.py")
        self.assertIn('.get("account_id")', source)
        self.assertNotIn('(displays.get(row.user) or {}).get("user")', source)
