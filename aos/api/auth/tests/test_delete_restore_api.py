from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.auth.delete_account import delete_account_impl, restore_account_impl
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAuthDeleteRestoreAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-delete-restore")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_delete_account_rejects_structured_confirmation(self):
        user = self.make_user("delete")
        frappe.set_user(user)
        with patch("aos.api.auth.delete_account.auth_rate_limit", return_value=None), patch("aos.api.auth.delete_account.auth_ip_limit", return_value=None):
            response = delete_account_impl(confirmation={"text": "DELETE"}, reason="test")
        self.assertEqual(response.get("error"), "VALIDATION_ERROR")

    def test_restore_missing_wrong_or_unrequested_otp_is_generic(self):
        user = self.make_user("restore-no-otp")
        frappe.db.set_value("AOS Profile", {"user": user}, {"account_status": "Deleted", "purge_status": "Pending"})
        frappe.db.commit()
        with patch("aos.api.auth.delete_account.auth_rate_limit", return_value=None), patch("aos.api.auth.delete_account.auth_ip_limit", return_value=None):
            missing = restore_account_impl(email=f"{self.prefix}-missing@example.com", otp="000000")
            no_otp = restore_account_impl(email=user, otp="000000")
        for response in (missing, no_otp):
            self.assertEqual(response.get("error"), "OTP_INVALID")
            self.assertEqual(response.get("message"), "Invalid or expired OTP.")

    def test_unknown_fields_are_rejected(self):
        response = restore_account_impl(email="person@example.com", otp="123456", verification_code="legacy")
        self.assertEqual(response.get("error"), "AUTH_UNKNOWN_FIELD")
