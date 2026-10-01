"""Static checks that Shorts analytics remains safe on large daily event tables."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestAnalyticsRollupSourceGuards(unittest.TestCase):
    def test_daily_counts_use_sargable_half_open_ranges(self):
        source = (ROOT / "aos/services/analytics_service.py").read_text()
        self.assertNotIn("DATE(creation)", source)
        self.assertIn("creation >= %s AND creation < %s", source)
        self.assertIn("day + timedelta(days=1)", source)
        self.assertIn('raise ValueError("Unsupported Short metrics source")', source)

    def test_source_indexes_match_bounded_daily_rollup_filters(self):
        indexes = (ROOT / "aos/patches/v1_0/install_shorts_indexes.py").read_text()
        for name in (
            "idx_short_event_rollup", "idx_short_like_rollup",
            "idx_short_save_rollup", "idx_short_repost_rollup",
            "idx_short_comment_rollup",
        ):
            with self.subTest(index=name):
                self.assertIn(f'"{name}"', indexes)

    def test_per_day_insert_races_use_database_uniqueness(self):
        service = (ROOT / "aos/services/analytics_service.py").read_text()
        indexes = (ROOT / "aos/patches/v1_0/install_shorts_indexes.py").read_text()
        self.assertIn('"uq_short_metrics_day", ("short", "date"), True', indexes)
        self.assertIn("except frappe.DuplicateEntryError:", service)
        self.assertIn('frappe.db.get_value("AOS Short Metrics Daily", key, "name")', service)

    def test_hot_state_flush_is_once_per_task_batch(self):
        service = (ROOT / "aos/services/analytics_service.py").read_text()
        tasks = (ROOT / "aos/tasks/shorts.py").read_text()
        self.assertNotIn("flush_hot_metrics(", service)
        self.assertIn("flush_hot_metrics(limit=2000)", tasks)
        self.assertIn("flush_hot_metrics(limit=limit)", tasks)


if __name__ == "__main__":
    unittest.main()
