from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.tasks import outbox as outbox_tasks


class TestOutboxPublisherDeadlockRetry(FrappeTestCase):
    def test_publisher_retries_transient_deadlock_after_rollback(self):
        expected = {"claimed": 0, "dispatched": 0}
        deadlock = frappe.QueryDeadlockError("simulated deadlock")

        with (
            patch.object(
                outbox_tasks,
                "publish_outbox_records",
                side_effect=[deadlock, deadlock, expected],
            ) as publish,
            patch.object(frappe.db, "rollback") as rollback,
            patch.object(outbox_tasks.time, "sleep") as sleep,
        ):
            result = outbox_tasks.publish_transactional_outbox(limit=17)

        self.assertEqual(result, expected)
        self.assertEqual(publish.call_count, 3)
        self.assertEqual(rollback.call_count, 2)
        self.assertEqual(sleep.call_count, 2)
        publish.assert_called_with(limit=17)

    def test_publisher_reraises_after_bounded_deadlock_retries(self):
        deadlock = frappe.QueryDeadlockError("simulated deadlock")
        with (
            patch.object(
                outbox_tasks,
                "publish_outbox_records",
                side_effect=[deadlock, deadlock, deadlock],
            ),
            patch.object(frappe.db, "rollback") as rollback,
            patch.object(outbox_tasks.time, "sleep") as sleep,
        ):
            with self.assertRaises(frappe.QueryDeadlockError):
                outbox_tasks.publish_transactional_outbox(limit=5)

        self.assertEqual(rollback.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
