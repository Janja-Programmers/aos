from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.api.auth.otp import resend_email_otp_impl, verify_email_otp_impl
from aos.api.auth.verification import ensure_ver_doc, hash_otp
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAuthOtpAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-otp")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _verification_user(self, label, *, otp="123456", expired=False, used=False):
        user = self.make_user(label, enabled=0)
        ver = ensure_ver_doc(user, email=user, purpose="email_verification")
        ver.otp_password_hash = hash_otp(otp)
        ver.expires_at = add_to_date(now_datetime(), minutes=-1 if expired else 10)
        ver.is_used = int(used)
        ver.attempts = 0
        ver.save(ignore_permissions=True)
        frappe.db.commit()
        return user

    def test_public_failures_are_generic(self):
        no_row = self.make_user("no-row", enabled=0)
        wrong = self._verification_user("wrong")
        expired = self._verification_user("expired", expired=True)
        used = self._verification_user("used", used=True)
        with patch("aos.api.auth.otp.auth_rate_limit", return_value=None), patch("aos.api.auth.otp.auth_ip_limit", return_value=None):
            responses = [
                verify_email_otp_impl(email=f"{self.prefix}-missing@example.com", otp="000000"),
                verify_email_otp_impl(email=no_row, otp="000000"),
                verify_email_otp_impl(email=wrong, otp="000000"),
                verify_email_otp_impl(email=expired, otp="123456"),
                verify_email_otp_impl(email=used, otp="123456"),
            ]
        for response in responses:
            self.assertEqual(response.get("error"), "OTP_INVALID")
            self.assertEqual(response.get("message"), "Invalid or expired OTP.")

    def test_valid_otp_is_single_use_and_activates_user(self):
        user = self._verification_user("valid", otp="654321")
        with patch("aos.api.auth.otp.auth_rate_limit", return_value=None), patch("aos.api.auth.otp.auth_ip_limit", return_value=None):
            first = verify_email_otp_impl(email=user, otp="654321")
            replay = verify_email_otp_impl(email=user, otp="654321")
        self.assertTrue(first.get("ok"), first)
        self.assertEqual(int(frappe.db.get_value("User", user, "enabled") or 0), 1)
        self.assertEqual(replay.get("error"), "OTP_INVALID")

    def test_resend_unknown_active_and_cooldown_share_generic_response(self):
        active = self.make_user("active")
        pending = self._verification_user("pending")
        pending_ver = ensure_ver_doc(pending, email=pending, purpose="email_verification")
        pending_ver.last_sent_at = now_datetime()
        pending_ver.save(ignore_permissions=True)
        frappe.db.commit()
        with patch("aos.api.auth.otp.auth_rate_limit", return_value=None), patch("aos.api.auth.otp.auth_ip_limit", return_value=None), patch("aos.api.auth.otp.issue_otp") as issue:
            unknown = resend_email_otp_impl(email=f"{self.prefix}-missing@example.com")
            active_response = resend_email_otp_impl(email=active)
            cooldown = resend_email_otp_impl(email=pending)
        self.assertEqual(unknown.get("message"), active_response.get("message"))
        self.assertEqual(active_response.get("message"), cooldown.get("message"))
        issue.assert_not_called()

    def test_unknown_fields_are_rejected(self):
        response = verify_email_otp_impl(email="x@example.com", otp="123456", code="legacy")
        self.assertEqual(response.get("error"), "AUTH_UNKNOWN_FIELD")

    def test_missing_otp_record_executes_dummy_slow_hash_work(self):
        from aos.api.auth.otp_service import verify_public_otp

        with patch("aos.api.auth.otp_service.verify_otp_password_hash", return_value=False) as verify_hash:
            response = verify_public_otp(None, "000000")
        self.assertEqual(response.get("error"), "OTP_INVALID")
        verify_hash.assert_called_once()
