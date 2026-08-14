from __future__ import annotations

import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.notifications.notification import (
    clear_notifications_impl,
    delete_notification_impl,
    list_notifications_impl,
    mark_all_notifications_read_impl,
    mark_notification_read_impl,
)
from aos.api.notifications.token import deactivate_push_token_impl, register_push_token_impl
from aos.services.account_deletion_service import (
    _cancel_notification_delivery_jobs,
    _deactivate_push_tokens,
    _remove_push_tokens,
)
from aos.services.accounts.identity import ensure_public_account_id
from aos.services.notification_delivery_service import create_notification_delivery_job
from aos.services.notification_service import NotificationService
from aos.services.notifications.devices import get_token_hash
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestNotificationDatabase(AOSFeatureTestMixin, FrappeTestCase):
    """DB-backed Notification behavior on a migrated Frappe site."""

    def setUp(self):
        self.prefix = self.make_prefix("notification")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.owner = self.make_user("owner")
        self.actor = self.make_user("actor")
        self.other = self.make_user("other")
        ensure_public_account_id(self.owner)
        ensure_public_account_id(self.actor)
        ensure_public_account_id(self.other)
        frappe.set_user(self.owner)

    def tearDown(self):
        frappe.set_user("Administrator")
        email_like = f"{self.prefix}-%@example.com"
        jobs = frappe.get_all(
            "AOS Notification Delivery Job",
            filters={"user": ["like", email_like]},
            pluck="name",
            limit=0,
        )
        if jobs:
            frappe.db.sql(
                "DELETE FROM `tabAOS Transactional Outbox` WHERE job_doctype = 'AOS Notification Delivery Job' AND job_name IN %(jobs)s",
                {"jobs": tuple(jobs)},
            )
        frappe.db.sql(
            "DELETE FROM `tabAOS Notification Delivery Job` WHERE user LIKE %s",
            (email_like,),
        )
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    @staticmethod
    def _without_inbox_limits():
        return patch("aos.api.notifications.notification.rate_limit", return_value=None)

    @staticmethod
    def _without_device_limits():
        return patch("aos.api.notifications.token.rate_limit", return_value=None)

    def _notify_follow(self, *, user: str | None = None, actor: str | None = None, dedupe_key: str | None = None):
        with patch("aos.services.notification_service.NotificationService._deliver"):
            return NotificationService.notify_follow(
                user=user or self.owner,
                follower=actor or self.actor,
                dedupe_key=dedupe_key or f"{self.prefix}:follow:{uuid.uuid4().hex}",
            )

    def test_authentication_required_for_inbox_and_device_endpoints(self):
        frappe.set_user("Guest")
        with self._without_inbox_limits():
            self.assertFalse(list_notifications_impl().get("ok"))
            self.assertFalse(mark_notification_read_impl(notification_id="missing").get("ok"))
            self.assertFalse(mark_all_notifications_read_impl().get("ok"))
            self.assertFalse(delete_notification_impl(notification_id="missing").get("ok"))
            self.assertFalse(clear_notifications_impl().get("ok"))
        with self._without_device_limits():
            self.assertFalse(
                register_push_token_impl(
                    token="fcm_test_token_abcdefghijklmnopqrstuvwxyz_0123456789",
                    device_type="android",
                ).get("ok")
            )

    def test_owner_only_inbox_read_delete_and_strict_unknown_fields(self):
        notification = self._notify_follow()
        self.assertTrue(notification)

        with self._without_inbox_limits():
            listed = list_notifications_impl(category="activity", limit=10)
            unknown = list_notifications_impl(unexpected="x")
        self.assertTrue(listed.get("ok"), listed)
        self.assertIn(notification.name, {item["id"] for item in listed["data"]["items"]})
        self.assertEqual(unknown.get("error"), "VALIDATION_ERROR")
        self.assertNotIn(self.owner, repr(listed["data"]["items"]))

        frappe.set_user(self.other)
        with self._without_inbox_limits():
            cross_read = mark_notification_read_impl(notification_id=notification.name)
            cross_delete = delete_notification_impl(notification_id=notification.name)
            cross_list = list_notifications_impl(category="activity")
        self.assertEqual(cross_read.get("error"), "NOT_FOUND")
        self.assertEqual(cross_delete.get("error"), "NOT_FOUND")
        self.assertNotIn(notification.name, {item["id"] for item in cross_list["data"]["items"]})
        self.assertTrue(frappe.db.exists("AOS Notification", notification.name))

    def test_mark_read_and_mark_all_are_idempotent_and_owner_scoped(self):
        first = self._notify_follow(dedupe_key=f"{self.prefix}:first")
        second = self._notify_follow(dedupe_key=f"{self.prefix}:second")
        with self._without_inbox_limits():
            one = mark_notification_read_impl(notification_id=first.name)
            repeated = mark_notification_read_impl(notification_id=first.name)
            all_read = mark_all_notifications_read_impl()
        self.assertTrue(one.get("ok"), one)
        self.assertTrue(repeated.get("ok"), repeated)
        self.assertTrue(all_read.get("ok"), all_read)
        self.assertEqual(int(frappe.db.get_value("AOS Notification", first.name, "is_read") or 0), 1)
        self.assertEqual(int(frappe.db.get_value("AOS Notification", second.name, "is_read") or 0), 1)

    def test_cursor_pagination_is_stable_when_creation_timestamps_tie(self):
        docs = [
            self._notify_follow(dedupe_key=f"{self.prefix}:page:{index}")
            for index in range(3)
        ]
        same_time = "2026-08-14 10:00:00.000000"
        for doc in docs:
            frappe.db.set_value(
                "AOS Notification", doc.name, "creation", same_time, update_modified=False
            )

        with self._without_inbox_limits():
            first = list_notifications_impl(category="activity", limit=1)
            second = list_notifications_impl(
                category="activity", limit=1, before=first["data"]["next_cursor"]
            )
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertNotEqual(first["data"]["items"][0]["id"], second["data"]["items"][0]["id"])

    def test_notification_dedupe_and_outbox_are_atomic_under_caller_transaction(self):
        outer = f"notification_outer_{uuid.uuid4().hex[:10]}"
        frappe.db.savepoint(outer)
        doc = NotificationService.notify_follow(
            user=self.owner,
            follower=self.actor,
            dedupe_key=f"{self.prefix}:atomic-follow",
        )
        self.assertTrue(doc)
        job = frappe.db.get_value(
            "AOS Notification Delivery Job",
            {"notification": doc.name},
            "name",
        )
        self.assertTrue(job)
        self.assertTrue(
            frappe.db.exists(
                "AOS Transactional Outbox",
                {"job_doctype": "AOS Notification Delivery Job", "job_name": job},
            )
        )
        frappe.db.rollback(save_point=outer)
        self.assertFalse(frappe.db.exists("AOS Notification", doc.name))
        self.assertFalse(frappe.db.exists("AOS Notification Delivery Job", job))
        self.assertFalse(
            frappe.db.exists(
                "AOS Transactional Outbox",
                {"job_doctype": "AOS Notification Delivery Job", "job_name": job},
            )
        )

    def test_notification_infrastructure_failure_rolls_back_only_notification_savepoint(self):
        with patch(
            "aos.services.notification_service.NotificationService._deliver",
            side_effect=RuntimeError("outbox unavailable"),
        ):
            doc = NotificationService.notify_follow(
                user=self.owner,
                follower=self.actor,
                dedupe_key=f"{self.prefix}:failed-intent",
            )
        self.assertIsNone(doc)
        self.assertFalse(
            frappe.db.exists(
                "AOS Notification",
                {"dedupe_key": f"{self.prefix}:failed-intent"},
            )
        )
        # The surrounding transaction remains usable after the local rollback.
        marker = self._notify_follow(dedupe_key=f"{self.prefix}:after-failure")
        self.assertTrue(marker)

    def test_duplicate_notification_retry_repairs_missing_delivery_job(self):
        dedupe_key = f"{self.prefix}:repair-missing-job"
        notification = NotificationService._create_notification(
            user=self.owner,
            type="follow",
            title="New Follower",
            body="Feature Actor started following you",
            actor=self.actor,
            payload={"follower": ensure_public_account_id(self.actor)},
            dedupe_key=dedupe_key,
        )
        self.assertTrue(notification)
        self.assertFalse(
            frappe.db.exists(
                "AOS Notification Delivery Job",
                {"notification": notification.name},
            )
        )

        retried = NotificationService.notify_follow(
            user=self.owner,
            follower=self.actor,
            dedupe_key=dedupe_key,
        )
        self.assertEqual(retried.name, notification.name)
        job = frappe.db.get_value(
            "AOS Notification Delivery Job",
            {"notification": notification.name},
            "name",
        )
        self.assertTrue(job)
        self.assertEqual(
            frappe.db.count(
                "AOS Transactional Outbox",
                {"job_doctype": "AOS Notification Delivery Job", "job_name": job},
            ),
            1,
        )

    def test_delivery_job_is_deduplicated_by_notification_identity(self):
        notification = self._notify_follow(dedupe_key=f"{self.prefix}:delivery-dedupe")
        kwargs = dict(
            user=self.owner,
            event="aos_follow",
            title="New Follower",
            body="Feature Actor started following you",
            payload={"follower": ensure_public_account_id(self.actor)},
            notification_id=notification.name,
            delivery_kind="persistent",
            enqueue=True,
        )
        first = create_notification_delivery_job(**kwargs)
        second = create_notification_delivery_job(**kwargs)
        self.assertEqual(first.name, second.name)
        self.assertEqual(
            frappe.db.count("AOS Notification Delivery Job", {"notification": notification.name}),
            1,
        )
        self.assertEqual(
            frappe.db.count(
                "AOS Transactional Outbox",
                {"job_doctype": "AOS Notification Delivery Job", "job_name": first.name},
            ),
            1,
        )

    def test_device_registration_rotation_cross_account_takeover_and_owner_only_deactivation(self):
        token_one = "fcm_test_token_one_abcdefghijklmnopqrstuvwxyz_0123456789"
        token_two = "fcm_test_token_two_abcdefghijklmnopqrstuvwxyz_0123456789"
        device_id = f"{self.prefix}-device-01"
        with self._without_device_limits():
            first = register_push_token_impl(
                token=token_one, device_type="android", device_id=device_id
            )
            rotated = register_push_token_impl(
                token=token_two, device_type="android", device_id=device_id
            )
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(rotated.get("ok"), rotated)
        row_name = rotated["data"]["id"]
        row = frappe.db.get_value(
            "AOS Push Token", row_name, ["user", "token_hash", "is_active"], as_dict=True
        )
        self.assertEqual(row.user, self.owner)
        self.assertEqual(row.token_hash, get_token_hash(token_two))
        self.assertEqual(int(row.is_active or 0), 1)
        self.assertNotIn(token_two, repr(rotated))

        frappe.set_user(self.other)
        with self._without_device_limits():
            takeover = register_push_token_impl(
                token=token_two, device_type="android", device_id=device_id
            )
        self.assertTrue(takeover.get("ok"), takeover)
        self.assertEqual(frappe.db.get_value("AOS Push Token", row_name, "user"), self.other)

        frappe.set_user(self.owner)
        with self._without_device_limits():
            hidden = deactivate_push_token_impl(token=token_two)
        self.assertTrue(hidden.get("ok"), hidden)
        self.assertEqual(int(frappe.db.get_value("AOS Push Token", row_name, "is_active") or 0), 1)

        frappe.set_user(self.other)
        with self._without_device_limits():
            deactivated = deactivate_push_token_impl(token=token_two)
        self.assertTrue(deactivated.get("ok"), deactivated)
        self.assertEqual(int(frappe.db.get_value("AOS Push Token", row_name, "is_active") or 0), 0)

    def test_account_deactivation_disables_device_tokens(self):
        token = "fcm_deactivate_token_abcdefghijklmnopqrstuvwxyz_0123456789"
        with self._without_device_limits():
            registered = register_push_token_impl(
                token=token,
                device_type="android",
                device_id=f"{self.prefix}-deactivate-device",
            )
        self.assertTrue(registered.get("ok"), registered)
        from frappe.utils import now_datetime

        count = _deactivate_push_tokens(user=self.owner, now=now_datetime())
        self.assertEqual(count, 1)
        self.assertEqual(
            int(
                frappe.db.get_value(
                    "AOS Push Token", registered["data"]["id"], "is_active"
                )
                or 0
            ),
            0,
        )

    def test_account_deletion_cancels_pending_jobs_and_removes_device_tokens(self):
        token = "fcm_delete_token_abcdefghijklmnopqrstuvwxyz_0123456789"
        with self._without_device_limits():
            registered = register_push_token_impl(
                token=token,
                device_type="android",
                device_id=f"{self.prefix}-delete-device",
            )
        self.assertTrue(registered.get("ok"), registered)

        notification = self._notify_follow(dedupe_key=f"{self.prefix}:delete-job")
        job = create_notification_delivery_job(
            user=self.owner,
            event="aos_follow",
            title="New Follower",
            body="Feature Actor started following you",
            payload={"follower": ensure_public_account_id(self.actor)},
            notification_id=notification.name,
            delivery_kind="persistent",
            enqueue=True,
        )
        frappe.set_user("Administrator")
        from frappe.utils import now_datetime

        cancelled = _cancel_notification_delivery_jobs(user=self.owner, now=now_datetime())
        removed = _remove_push_tokens(user=self.owner)
        self.assertGreaterEqual(cancelled, 1)
        self.assertGreaterEqual(removed, 1)
        self.assertEqual(frappe.db.get_value("AOS Notification Delivery Job", job.name, "status"), "Cancelled")
        self.assertFalse(frappe.db.exists("AOS Push Token", registered["data"]["id"]))


