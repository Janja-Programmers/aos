from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime
from unittest.mock import patch

from aos.services.account_purge_service import purge_expired_deleted_account
from aos.services.accounts.lifecycle_service import AccountLifecycleService
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.accounts.serializers import serialize_public_profile
from aos.services.social.service import SocialService
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestRecoverableAccountDeletion(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("recoverable-delete")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        self.cleanup_feature_rows()
        self.restore_localization_test_state()
        frappe.set_user("Administrator")

    def _follow(self, *, follower: str, following: str) -> str:
        with patch(
            "aos.services.social.service.SocialService._notify_follow_atomic",
            return_value="test",
        ):
            result = SocialService().follow(
                actor=follower,
                payload={
                    "account_id": public_account_id_for_user(following),
                },
            )
        self.assertTrue(result["changed"], result)
        edge = frappe.db.get_value(
            "AOS Follow",
            {"follower_user": follower, "following_user": following},
            "name",
        )
        self.assertTrue(edge)
        return str(edge)

    def test_delete_restore_preserves_social_graph_counts_and_verification(self):
        owner = self.make_user("owner")
        follower = self.make_user("follower")
        blocked_user = self.make_user("blocked")
        edge = self._follow(follower=follower, following=owner)

        # AOS Social intentionally removes follow edges when either participant
        # blocks the other. Exercise follower preservation and block preservation
        # with independent relationships so this fixture does not invalidate its
        # own follower before the account-deletion assertions begin.
        block = frappe.get_doc(
            {
                "doctype": "AOS User Block",
                "blocker_user": owner,
                "blocked_user": blocked_user,
                "status": "Active",
            }
        )
        block.insert(ignore_permissions=True)
        frappe.db.set_value("AOS Profile", {"user": owner}, "is_verified", 1, update_modified=False)

        before = frappe.db.get_value(
            "AOS Profile", {"user": owner}, ["total_followers", "is_verified"], as_dict=True
        )
        self.assertEqual(int(before.total_followers or 0), 1)
        self.assertEqual(int(before.is_verified or 0), 1)

        deleted = AccountLifecycleService().delete(user=owner, reason="test")
        self.assertEqual(deleted["status"], "Deleted")
        self.assertTrue(frappe.db.exists("AOS Follow", edge))
        self.assertEqual(frappe.db.get_value("AOS User Block", block.name, "status"), "Active")
        during = frappe.db.get_value(
            "AOS Profile",
            {"user": owner},
            ["account_status", "total_followers", "is_verified", "purge_status"],
            as_dict=True,
        )
        self.assertEqual(during.account_status, "Deleted")
        self.assertEqual(int(during.total_followers or 0), 1)
        self.assertEqual(int(during.is_verified or 0), 1)
        self.assertEqual(during.purge_status, "Pending")
        public_deleted = serialize_public_profile(owner)
        self.assertEqual(public_deleted["followers_count"], 0)
        self.assertFalse(public_deleted["is_verified"])

        restored = AccountLifecycleService().restore(user=owner)
        self.assertEqual(restored["status"], "Active")
        self.assertTrue(frappe.db.exists("AOS Follow", edge))
        self.assertEqual(frappe.db.get_value("AOS User Block", block.name, "status"), "Active")
        after = serialize_public_profile(owner)
        self.assertEqual(after["followers_count"], 1)
        self.assertTrue(after["is_verified"])

    def test_expired_account_social_graph_is_purged_in_bounded_batches(self):
        owner = self.make_user("purge-owner")
        follower_a = self.make_user("follower-a")
        follower_b = self.make_user("follower-b")
        self._follow(follower=follower_a, following=owner)
        self._follow(follower=follower_b, following=owner)

        AccountLifecycleService().delete(user=owner, reason="test")
        frappe.db.set_value(
            "AOS Profile",
            {"user": owner},
            {
                "restore_deadline": add_to_date(now_datetime(), days=-1),
                "purge_status": "Pending",
            },
            update_modified=False,
        )

        first = purge_expired_deleted_account(owner, batch_size=1)
        self.assertFalse(first["completed"])
        self.assertEqual(frappe.db.count("AOS Follow", {"following_user": owner}), 1)
        self.assertEqual(frappe.db.get_value("AOS Profile", {"user": owner}, "purge_status"), "Purging")

        for _ in range(5):
            result = purge_expired_deleted_account(owner, batch_size=1)
            if result["completed"]:
                break
        self.assertTrue(result["completed"])
        self.assertEqual(frappe.db.count("AOS Follow", {"following_user": owner}), 0)
        profile = frappe.db.get_value(
            "AOS Profile", {"user": owner}, ["purge_status", "display_name", "is_verified"], as_dict=True
        )
        self.assertEqual(profile.purge_status, "Completed")
        self.assertEqual(profile.display_name, "Deleted User")
        self.assertEqual(int(profile.is_verified or 0), 0)
