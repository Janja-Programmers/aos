from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import update_password

from aos.api.auth.password_change import change_password_impl
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAuthPasswordChangeAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-password-change")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.user = self.make_user("user")
        update_password(self.user, "CurrentStrong123!")
        frappe.set_user(self.user)

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_change_password_success_keeps_current_sid_and_revokes_other_sessions(self):
        frappe.session.sid = "current-sid"
        with patch("aos.api.auth.password_change.auth_rate_limit", return_value=None), patch("aos.api.auth.password_change.auth_ip_limit", return_value=None), patch(
            "aos.api.auth.password_change.revoke_all_sessions", return_value=2
        ) as revoke:
            response = change_password_impl(
                current_password="CurrentStrong123!",
                new_password="NewStrongPass123!",
                confirm_password="NewStrongPass123!",
            )
        self.assertTrue(response.get("ok"), response)
        revoke.assert_called_once_with(self.user, keep_sid="current-sid")

    def test_invalid_current_password_is_stable(self):
        with patch("aos.api.auth.password_change.auth_rate_limit", return_value=None), patch("aos.api.auth.password_change.auth_ip_limit", return_value=None):
            response = change_password_impl(
                current_password="WrongStrong123!",
                new_password="NewStrongPass123!",
                confirm_password="NewStrongPass123!",
            )
        self.assertEqual(response.get("error"), "FORBIDDEN")

    def test_password_mismatch_and_unknown_field(self):
        mismatch = change_password_impl(current_password="x", new_password="NewStrongPass123!", confirm_password="DifferentStrong123!")
        legacy = change_password_impl(current_password="x", new_password="NewStrongPass123!", confirm_password="NewStrongPass123!", logout_all=True)
        self.assertEqual(mismatch.get("error"), "PASSWORD_MISMATCH")
        self.assertEqual(legacy.get("error"), "AUTH_UNKNOWN_FIELD")

    def test_reused_new_password_is_rejected(self):
        with patch("aos.api.auth.password_change.auth_rate_limit", return_value=None), patch("aos.api.auth.password_change.auth_ip_limit", return_value=None):
            response = change_password_impl(
                current_password="CurrentStrong123!",
                new_password="CurrentStrong123!",
                confirm_password="CurrentStrong123!",
            )
        self.assertEqual(response.get("error"), "PASSWORD_REUSED")
