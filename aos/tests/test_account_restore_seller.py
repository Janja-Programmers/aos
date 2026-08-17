from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.services.account_deletion_service import _mark_sellers_deleted, restore_deleted_account_features
from aos.services.sellers.policy import set_seller_status
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAccountRestoreSeller(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("restore-seller")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        self.cleanup_feature_rows()
        self.restore_localization_test_state()
        frappe.set_user("Administrator")

    def test_active_seller_deleted_by_account_restore_returns_active(self):
        user = self.make_user("active")
        seller = self.make_seller(user)

        self.assertEqual(_mark_sellers_deleted(sellers=[seller.name]), 1)
        seller.reload()
        self.assertEqual(seller.status, "Deleted")
        self.assertEqual(seller.account_delete_previous_status, "Active")

        summary = restore_deleted_account_features(user)
        seller.reload()
        self.assertEqual(seller.status, "Active")
        self.assertFalse(seller.account_delete_previous_status)
        self.assertEqual(summary.get("seller_profiles_restored"), 1)
        self.assertEqual(summary.get("seller_requires_reactivation"), 0)

    def test_suspended_seller_restores_to_suspended_not_active(self):
        user = self.make_user("suspended")
        seller = self.make_seller(user)
        set_seller_status(
            seller.name,
            status="Suspended",
            reason_code="TEST_SUSPENSION",
            source="test",
            actor="Administrator",
        )

        self.assertEqual(_mark_sellers_deleted(sellers=[seller.name]), 1)
        seller.reload()
        self.assertEqual(seller.account_delete_previous_status, "Suspended")

        restore_deleted_account_features(user)
        seller.reload()
        self.assertEqual(seller.status, "Suspended")
        self.assertFalse(seller.account_delete_previous_status)
