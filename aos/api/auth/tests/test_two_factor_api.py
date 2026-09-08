from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.api.auth.two_factor import issue_two_factor_challenge, verify_two_factor_impl
from aos.api.auth.verification import TWO_FACTOR_PURPOSE, get_ver_doc, hash_otp
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


_MISSING_LOGIN_MANAGER = object()
_MISSING_RESPONSE = object()


class TestAuthTwoFactorAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-2fa")
        self.created_users: list[str] = []
        self._original_login_manager = getattr(frappe.local, "login_manager", _MISSING_LOGIN_MANAGER)
        self._original_response = getattr(frappe.local, "response", _MISSING_RESPONSE)
        self._original_sid = getattr(frappe.session, "sid", None)
        frappe.local.response = {}
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        try:
            self.cleanup_feature_rows()
        finally:
            if self._original_login_manager is _MISSING_LOGIN_MANAGER:
                try:
                    delattr(frappe.local, "login_manager")
                except AttributeError:
                    pass
            else:
                frappe.local.login_manager = self._original_login_manager
            if self._original_response is _MISSING_RESPONSE:
                try:
                    delattr(frappe.local, "response")
                except AttributeError:
                    pass
            else:
                frappe.local.response = self._original_response
            frappe.session.sid = self._original_sid
            frappe.set_user("Administrator")

    @staticmethod
    def _persist_known_otp(ver, **_kwargs):
        ver.otp_password_hash = hash_otp("123456")
        ver.expires_at = add_to_date(now_datetime(), minutes=10)
        ver.last_sent_at = now_datetime()
        ver.is_used = 0
        ver.attempts = 0
        ver.save(ignore_permissions=True)
        return "123456"

    @staticmethod
    def _manager(*, sid: str = "sid-2fa"):
        def post_login():
            frappe.session.sid = sid

        manager = SimpleNamespace(
            user=None,
            post_login=Mock(side_effect=post_login),
            force_user_to_reset_password=Mock(return_value=False),
        )
        frappe.local.login_manager = manager
        return manager

    def test_challenge_uses_digest_and_success_is_single_use(self):
        user = self.make_user("success")
        token = "2fa-continuation-token-" + ("x" * 48)
        with patch("aos.api.auth.two_factor.generate_continuation_token", return_value=token), patch(
            "aos.api.auth.two_factor.issue_otp", side_effect=self._persist_known_otp
        ):
            issued = issue_two_factor_challenge(user)

        self.assertEqual(issued.get("error"), "TWO_FACTOR_REQUIRED")
        self.assertEqual(issued["data"]["challenge_token"], token)
        challenge = get_ver_doc(user, TWO_FACTOR_PURPOSE)
        self.assertIsNotNone(challenge)
        self.assertNotEqual(challenge.continuation_token_hash, token)
        self.assertTrue(challenge.continuation_token_hash)

        manager = self._manager()
        with patch("aos.api.auth.two_factor.auth_ip_limit", return_value=None), patch(
            "aos.api.auth.two_factor.auth_rate_limit", return_value=None
        ):
            response = verify_two_factor_impl(challenge_token=token, otp="123456", client_type="mobile")
            replay = verify_two_factor_impl(challenge_token=token, otp="123456", client_type="mobile")

        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response["data"]["session"]["sid"], "sid-2fa")
        manager.post_login.assert_called_once_with()
        self.assertEqual(replay.get("error"), "TOKEN_INVALID")
        challenge.reload()
        self.assertEqual(int(challenge.is_used or 0), 1)
        self.assertFalse(challenge.otp_password_hash)
        self.assertFalse(challenge.continuation_token_hash)

    def test_invalid_otp_does_not_create_session_or_consume_continuation(self):
        user = self.make_user("invalid")
        token = "2fa-continuation-token-" + ("y" * 48)
        with patch("aos.api.auth.two_factor.generate_continuation_token", return_value=token), patch(
            "aos.api.auth.two_factor.issue_otp", side_effect=self._persist_known_otp
        ):
            issue_two_factor_challenge(user)

        manager = self._manager()
        with patch("aos.api.auth.two_factor.auth_ip_limit", return_value=None), patch(
            "aos.api.auth.two_factor.auth_rate_limit", return_value=None
        ):
            response = verify_two_factor_impl(challenge_token=token, otp="000000", client_type="web")

        self.assertEqual(response.get("error"), "OTP_INVALID")
        manager.post_login.assert_not_called()
        challenge = get_ver_doc(user, TWO_FACTOR_PURPOSE)
        self.assertEqual(int(challenge.is_used or 0), 0)
        self.assertTrue(challenge.continuation_token_hash)

    def test_repeated_challenge_with_active_otp_respects_resend_cooldown(self):
        user = self.make_user("cooldown")
        first_token = "2fa-continuation-token-" + ("a" * 48)
        second_token = "2fa-continuation-token-" + ("b" * 48)
        with patch(
            "aos.api.auth.two_factor.generate_continuation_token",
            side_effect=[first_token, second_token],
        ), patch("aos.api.auth.two_factor.issue_otp", side_effect=self._persist_known_otp) as issue:
            first = issue_two_factor_challenge(user)
            second = issue_two_factor_challenge(user)

        self.assertEqual(first.get("error"), "TWO_FACTOR_REQUIRED")
        self.assertEqual(second.get("error"), "TWO_FACTOR_REQUIRED")
        self.assertEqual(issue.call_count, 1)
        self.assertNotEqual(first["data"]["challenge_token"], second["data"]["challenge_token"])

    def test_max_attempts_forces_fresh_two_factor_otp_even_during_cooldown(self):
        user = self.make_user("max-attempts")
        with patch("aos.api.auth.two_factor.issue_otp", side_effect=self._persist_known_otp):
            issue_two_factor_challenge(user)
        challenge = get_ver_doc(user, TWO_FACTOR_PURPOSE)
        challenge.attempts = 5
        challenge.save(ignore_permissions=True)

        with patch("aos.api.auth.two_factor.issue_otp", side_effect=self._persist_known_otp) as issue:
            response = issue_two_factor_challenge(user)

        self.assertEqual(response.get("error"), "TWO_FACTOR_REQUIRED")
        issue.assert_called_once()

    def test_unknown_fields_are_rejected(self):
        response = verify_two_factor_impl(
            challenge_token="2fa-continuation-token-" + ("z" * 48),
            otp="123456",
            client_type="mobile",
            remember_me=True,
        )
        self.assertEqual(response.get("error"), "AUTH_UNKNOWN_FIELD")
