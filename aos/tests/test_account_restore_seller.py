from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.services.account_deletion_service import (
    restore_deleted_account_features,
    tombstone_deleted_account_features,
)
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

    def test_active_seller_state_is_preserved_across_recoverable_delete_restore(self):
        user = self.make_user("active")
        seller = self.make_seller(user)

        tombstone_deleted_account_features(user)
        seller.reload()
        self.assertEqual(seller.status, "Active")

        summary = restore_deleted_account_features(user)
        seller.reload()
        self.assertEqual(seller.status, "Active")
        self.assertEqual(summary.get("marketplace_state_restored"), 1)

    def test_suspended_seller_remains_suspended_across_recoverable_delete_restore(self):
        user = self.make_user("suspended")
        seller = self.make_seller(user)
        set_seller_status(
            seller.name,
            status="Suspended",
            reason_code="TEST_SUSPENSION",
            source="test",
            actor="Administrator",
        )

        tombstone_deleted_account_features(user)
        seller.reload()
        self.assertEqual(seller.status, "Suspended")

        restore_deleted_account_features(user)
        seller.reload()
        self.assertEqual(seller.status, "Suspended")
