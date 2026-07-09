from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.api.auth.otp import resend_email_otp_impl, verify_email_otp_impl
from aos.api.auth.verification import ensure_ver_doc, otp_hash
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAuthOtpAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-otp")
        self.created_users: list[str] = []
        frappe.set_user("Guest")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _make_verification_user(self, label: str, *, otp: str = "123456", expired: bool = False):
        user = self.make_user(label, enabled=0)
        ver = ensure_ver_doc(user, email=user, purpose="email_verification")
        ver.otp_hash = otp_hash(otp)
        ver.expires_at = add_to_date(now_datetime(), minutes=-1 if expired else 10)
        ver.is_used = 0
        ver.attempts = 0
        ver.save(ignore_permissions=True)
        frappe.db.commit()
        return user

    def test_verify_email_otp_rejects_structured_inputs(self):
        response = verify_email_otp_impl(email=["x@example.com"], otp="123456")
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "VALIDATION_ERROR")
        self.assertEqual(response.get("data", {}).get("field"), "email")

        response = verify_email_otp_impl(email="x@example.com", otp={"otp": "123456"})
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("data", {}).get("field"), "otp")

    def test_invalid_missing_nonexistent_and_expired_otp_are_generic(self):
        no_otp_user = self.make_user("no-otp", enabled=0)
        wrong_user = self._make_verification_user("wrong")
        expired_user = self._make_verification_user("expired", expired=True)

        with patch("aos.api.auth.otp.rate_limit", return_value=None):
            responses = [
                verify_email_otp_impl(email=f"{self.prefix}-missing@example.com", otp="000000"),
                verify_email_otp_impl(email=no_otp_user, otp="000000"),
                verify_email_otp_impl(email=wrong_user, otp="000000"),
                verify_email_otp_impl(email=expired_user, otp="123456"),
            ]

        for response in responses:
            self.assertFalse(response.get("ok"), response)
            self.assertEqual(response.get("error"), "OTP_INVALID")
            self.assertEqual(response.get("message"), "Invalid or expired OTP.")

    def test_valid_email_otp_activates_user(self):
        user = self._make_verification_user("valid", otp="654321")

        with patch("aos.api.auth.otp.rate_limit", return_value=None):
            response = verify_email_otp_impl(email=user, otp="654321")

        self.assertTrue(response.get("ok"), response)
        self.assertEqual(int(frappe.db.get_value("User", user, "enabled") or 0), 1)

    def test_resend_email_otp_does_not_leak_unknown_account(self):
        with patch("aos.api.auth.otp.rate_limit", return_value=None):
            response = resend_email_otp_impl(email=f"{self.prefix}-missing@example.com")

        self.assertTrue(response.get("ok"), response)
