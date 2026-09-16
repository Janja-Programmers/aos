from __future__ import annotations

from pathlib import Path
import unittest


_ROOT = Path(__file__).resolve().parents[3]


class SharedProductionBoundaryContracts(unittest.TestCase):
    def test_shared_rate_limit_is_atomic_and_has_no_read_modify_write_fallback(self):
        source = (_ROOT / "api/shared/rate_limit.py").read_text(encoding="utf-8")
        self.assertIn("cache.eval(_WINDOW_SCRIPT", source)
        self.assertIn("redis.call('INCR'", source)
        self.assertIn("redis.call('EXPIRE'", source)
        self.assertNotIn("cache.get_value", source)
        self.assertNotIn("cache.set_value", source)

    def test_shared_rate_limit_does_not_swallow_redis_failures(self):
        source = (_ROOT / "api/shared/rate_limit.py").read_text(encoding="utf-8")
        cache_incr = source.split("def cache_incr", 1)[1].split("\ndef rate_limit", 1)[0]
        self.assertNotIn("except", cache_incr)
        self.assertIn("return int(cache.eval", cache_incr)

    def test_completed_v1_wrappers_do_not_use_transitional_transport_helper(self):
        for feature in (
            "localization", "auth", "media", "accounts", "notifications",
            "verification", "social", "maps", "sellers", "catalog", "ads",
        ):
            with self.subTest(feature=feature):
                source = (_ROOT / f"api/v1/{feature}/__init__.py").read_text(encoding="utf-8")
                self.assertNotIn("aos.api.v1._transport", source)
                self.assertIn("aos.api.shared.transport", source)
