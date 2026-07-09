from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.shared.auth import require_login
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestSharedAuthHelpers(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-helper")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_require_login_rejects_guest(self):
        frappe.set_user("Guest")
        user, err = require_login()
        self.assertIsNone(user)
        self.assertFalse(err.get("ok"), err)
        self.assertEqual(err.get("error"), "UNAUTHORIZED")

    def test_require_login_rejects_disabled_user(self):
        user = self.make_user("disabled", enabled=0)
        frappe.set_user(user)
        current_user, err = require_login()
        self.assertIsNone(current_user)
        self.assertFalse(err.get("ok"), err)
        self.assertEqual(err.get("error"), "ACCOUNT_DISABLED")
