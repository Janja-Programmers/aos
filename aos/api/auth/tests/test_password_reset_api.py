from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.api.auth.password_reset import forgot_password_request_impl, forgot_password_verify_otp_impl
from aos.api.auth.verification import ensure_ver_doc, otp_hash
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAuthPasswordResetAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-password-reset")
        self.created_users: list[str] = []
        frappe.set_user("Guest")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _make_reset_user(self, label: str, *, otp: str = "123456", expired: bool = False):
        user = self.make_user(label)
        ver = ensure_ver_doc(user, email=user, purpose="password_reset")
        ver.otp_hash = otp_hash(otp)
        ver.expires_at = add_to_date(now_datetime(), minutes=-1 if expired else 10)
        ver.is_used = 0
        ver.attempts = 0
        ver.save(ignore_permissions=True)
        frappe.db.commit()
        return user

    def test_password_reset_request_rejects_structured_email(self):
        response = forgot_password_request_impl(email={"email": "x@example.com"})
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "VALIDATION_ERROR")
        self.assertEqual(response.get("data", {}).get("field"), "email")

    def test_password_reset_verify_rejects_structured_otp(self):
        response = forgot_password_verify_otp_impl(email="x@example.com", otp=["123456"])
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "VALIDATION_ERROR")
        self.assertEqual(response.get("data", {}).get("field"), "otp")

    def test_password_reset_verify_otp_generic_failures(self):
        no_otp_user = self.make_user("no-otp")
        wrong_user = self._make_reset_user("wrong")
        expired_user = self._make_reset_user("expired", expired=True)

        with patch("aos.api.auth.password_reset.rate_limit", return_value=None):
            responses = [
                forgot_password_verify_otp_impl(email=f"{self.prefix}-missing@example.com", otp="000000"),
                forgot_password_verify_otp_impl(email=no_otp_user, otp="000000"),
                forgot_password_verify_otp_impl(email=wrong_user, otp="000000"),
                forgot_password_verify_otp_impl(email=expired_user, otp="123456"),
            ]

        for response in responses:
            self.assertFalse(response.get("ok"), response)
            self.assertEqual(response.get("error"), "OTP_INVALID")
            self.assertEqual(response.get("message"), "Invalid or expired OTP.")

    def test_password_reset_verify_valid_otp_returns_reset_token(self):
        user = self._make_reset_user("valid", otp="654321")

        with patch("aos.api.auth.password_reset.rate_limit", return_value=None):
            response = forgot_password_verify_otp_impl(email=user, otp="654321")

        self.assertTrue(response.get("ok"), response)
        self.assertTrue(response.get("data", {}).get("reset_token"))
