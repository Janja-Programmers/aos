from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from frappe.tests.utils import FrappeTestCase

from aos.services.activity.producer import best_effort_activity
from aos.services.activity_service import ActivityService


class TestActivityConcurrencyContracts(FrappeTestCase):
    """Exercise the duplicate/rollback branches used by concurrent workers."""

    def test_concurrent_once_insert_returns_unique_index_winner(self):
        candidate = MagicMock()
        candidate.insert.side_effect = RuntimeError("simulated duplicate race")
        winner = {"name": "ROW-WINNER", "public_id": "ACT-0123456789abcdef0123456789abcdef"}

        with (
            patch.object(ActivityService, "_find_by_event_key", side_effect=[None, winner]),
            patch("aos.services.activity_service.frappe.get_doc", return_value=candidate),
            patch("aos.services.activity_service.is_duplicate_entry_error", return_value=True),
        ):
            result = ActivityService.record_activity(
                user="owner@example.test",
                activity_group="Social",
                activity_type="user_report",
                target_doctype="User",
                target_name="target@example.test",
                route_type="profile",
                route_id="ACC-AAAAAAAAAAAAAAAAAAAA",
                metadata={"target_user": "ACC-AAAAAAAAAAAAAAAAAAAA"},
                unique_key="logical-once-key",
            )

        self.assertEqual(result, winner["public_id"])
        candidate.insert.assert_called_once_with(ignore_permissions=True)

    def test_concurrent_coalesced_insert_updates_unique_index_winner(self):
        candidate = MagicMock()
        candidate.insert.side_effect = RuntimeError("simulated duplicate race")
        winner = {"name": "ROW-WINNER", "public_id": "ACT-fedcba9876543210fedcba9876543210"}

        with (
            patch.object(ActivityService, "_find_active_by_key", return_value=None),
            patch.object(ActivityService, "_lock_active_by_key", return_value=winner),
            patch.object(ActivityService, "_atomic_update_existing_activity") as update,
            patch("aos.services.activity_service.frappe.get_doc", return_value=candidate),
            patch("aos.services.activity_service.is_duplicate_entry_error", return_value=True),
        ):
            result = ActivityService.record_or_update_activity(
                user="owner@example.test",
                activity_group="Search",
                activity_type="user_search",
                route_type="user_search",
                route_id="shoes",
                metadata={"query": "shoes", "result_count": 2},
                unique_key="logical-coalesced-key",
            )

        self.assertEqual(result, winner["public_id"])
        candidate.insert.assert_called_once_with(ignore_permissions=True)
        update.assert_called_once()

    def test_optional_producer_failure_rolls_back_only_to_savepoint(self):
        operation = MagicMock(side_effect=RuntimeError("projection unavailable"))
        with (
            patch("aos.services.activity.producer.frappe.db.savepoint") as savepoint,
            patch("aos.services.activity.producer.frappe.db.rollback") as rollback,
            patch("aos.services.activity.producer.frappe.log_error"),
            patch("aos.services.activity.producer.activity_log"),
        ):
            result = best_effort_activity("test", operation, SimpleNamespace())

        self.assertIsNone(result)
        savepoint.assert_called_once()
        rollback.assert_called_once()


if __name__ == "__main__":
    import unittest

    unittest.main()
