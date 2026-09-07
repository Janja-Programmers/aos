from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.api.auth.password_reset import (
    forgot_password_request_impl,
    forgot_password_reset_impl,
    forgot_password_verify_otp_impl,
)
from aos.api.auth.verification import ensure_ver_doc, hash_otp
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAuthPasswordResetAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-reset")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _reset_user(self, label, *, otp="123456", expired=False):
        user = self.make_user(label)
        ver = ensure_ver_doc(user, purpose="password_reset")
        ver.otp_password_hash = hash_otp(otp)
        ver.expires_at = add_to_date(now_datetime(), minutes=-1 if expired else 10)
        ver.is_used = 0
        ver.attempts = 0
        ver.save(ignore_permissions=True)
        frappe.db.commit()
        return user

    def _no_limits(self):
        return patch.multiple(
            "aos.api.auth.password_reset",
            auth_rate_limit=patch.DEFAULT,
            auth_ip_limit=patch.DEFAULT,
        )

    def test_request_unknown_and_existing_share_enumeration_safe_response(self):
        user = self.make_user("existing")
        with patch("aos.api.auth.password_reset.auth_rate_limit", return_value=None), patch("aos.api.auth.password_reset.auth_ip_limit", return_value=None), patch("aos.api.auth.password_reset.issue_otp"):
            missing = forgot_password_request_impl(email=f"{self.prefix}-missing@example.com")
            existing = forgot_password_request_impl(email=user)
        self.assertTrue(missing.get("ok")); self.assertTrue(existing.get("ok"))
        self.assertEqual(missing.get("message"), existing.get("message"))

    def test_verify_failures_are_generic_and_valid_returns_high_entropy_token(self):
        wrong = self._reset_user("wrong")
        expired = self._reset_user("expired", expired=True)
        valid = self._reset_user("valid", otp="654321")
        with patch("aos.api.auth.password_reset.auth_rate_limit", return_value=None), patch("aos.api.auth.password_reset.auth_ip_limit", return_value=None):
            missing = forgot_password_verify_otp_impl(email=f"{self.prefix}-missing@example.com", otp="000000")
            wrong_response = forgot_password_verify_otp_impl(email=wrong, otp="000000")
            expired_response = forgot_password_verify_otp_impl(email=expired, otp="123456")
            good = forgot_password_verify_otp_impl(email=valid, otp="654321")
        for response in (missing, wrong_response, expired_response):
            self.assertEqual(response.get("error"), "OTP_INVALID")
        self.assertTrue(good.get("ok"), good)
        self.assertGreaterEqual(len(good["data"]["reset_token"]), 32)

    def test_reset_consumes_token_revokes_sessions_and_replay_fails(self):
        user = self._reset_user("consume", otp="777777")
        with patch("aos.api.auth.password_reset.auth_rate_limit", return_value=None), patch("aos.api.auth.password_reset.auth_ip_limit", return_value=None):
            verified = forgot_password_verify_otp_impl(email=user, otp="777777")
            token = verified["data"]["reset_token"]
            with patch("aos.api.auth.password_reset.revoke_all_sessions", return_value=2) as revoke:
                first = forgot_password_reset_impl(email=user, reset_token=token, new_password="NewStrongPass123!", confirm_password="NewStrongPass123!")
            replay = forgot_password_reset_impl(email=user, reset_token=token, new_password="AnotherStrong123!", confirm_password="AnotherStrong123!")
        self.assertTrue(first.get("ok"), first)
        revoke.assert_called_once_with(user)
        self.assertEqual(replay.get("error"), "TOKEN_INVALID")

    def test_expired_reset_token_is_stable(self):
        user = self._reset_user("token-expired")
        ver = ensure_ver_doc(user, purpose="password_reset")
        from aos.api.auth.verification import token_digest
        ver.continuation_token_hash = token_digest("expired-token")
        ver.continuation_expires_at = add_to_date(now_datetime(), minutes=-1)
        ver.save(ignore_permissions=True)
        with patch("aos.api.auth.password_reset.auth_rate_limit", return_value=None), patch("aos.api.auth.password_reset.auth_ip_limit", return_value=None):
            response = forgot_password_reset_impl(email=user, reset_token="expired-token", new_password="NewStrongPass123!", confirm_password="NewStrongPass123!")
        self.assertEqual(response.get("error"), "TOKEN_EXPIRED")

    def test_reset_rejects_password_reuse_after_valid_token(self):
        from frappe.utils.password import update_password

        user = self._reset_user("reuse", otp="333333")
        update_password(user, "CurrentStrong123!")
        with patch("aos.api.auth.password_reset.auth_rate_limit", return_value=None), patch("aos.api.auth.password_reset.auth_ip_limit", return_value=None):
            verified = forgot_password_verify_otp_impl(email=user, otp="333333")
            response = forgot_password_reset_impl(
                email=user,
                reset_token=verified["data"]["reset_token"],
                new_password="CurrentStrong123!",
                confirm_password="CurrentStrong123!",
            )
        self.assertEqual(response.get("error"), "PASSWORD_REUSED")
