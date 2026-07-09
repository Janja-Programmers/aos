from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.exceptions import AuthenticationError
from frappe.tests.utils import FrappeTestCase

from aos.api.auth.session import login_impl, logout_impl, me_impl
from aos.api.shared.auth import require_login
from aos.api.shared.responses import fail
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAuthSharedHardening(AOSFeatureTestMixin, FrappeTestCase):
    """Security-sensitive auth/session/shared contract tests."""

    def setUp(self):
        self.prefix = self.make_prefix("auth-hardening")
        self.created_users: list[str] = []
        frappe.local.response = {}
        frappe.set_user("Administrator")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _install_fake_login_manager(self, *, sid: str = "sid-test-value"):
        def _post_login():
            frappe.session.sid = sid

        frappe.local.login_manager = SimpleNamespace(
            authenticate=Mock(),
            post_login=Mock(side_effect=_post_login),
            logout=Mock(side_effect=lambda: frappe.set_user("Guest")),
        )
        return frappe.local.login_manager

    def test_response_failure_shape_has_canonical_error(self):
        response = fail("Bad request.", error="VALIDATION_ERROR")

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "VALIDATION_ERROR")
        self.assertNotIn("code", response)
        self.assertEqual(response["data"], {})

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

    def test_me_requires_valid_session_for_guest(self):
        frappe.set_user("Guest")

        response = me_impl()

        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "SESSION_INVALID")

    def test_me_repairs_missing_user_preference_and_returns_safe_shape(self):
        user = self.make_user("missing-pref", with_preference=False)
        frappe.set_user(user)

        response = me_impl()

        self.assertTrue(response.get("ok"), response)
        data = response.get("data", {})
        self.assertEqual(data.get("user", {}).get("email"), user)
        self.assertIn("preferences", data)
        self.assertIn("seller", data)
        self.assertNotIn("sid", data.get("session", {}))
        self.assertTrue(frappe.db.exists("AOS User Preference", {"user": user}))

    def test_login_wrong_password_and_unknown_identifier_are_not_enumerable(self):
        user = self.make_user("wrong-password")

        with patch("aos.api.auth.session._rate_limit_login", return_value=None):
            unknown = login_impl(identifier=f"{self.prefix}-missing@example.com", password="WrongPass123!", client_type="mobile")

        with (
            patch("aos.api.auth.session._rate_limit_login", return_value=None),
            patch("aos.api.auth.session.check_password", side_effect=AuthenticationError),
        ):
            wrong = login_impl(identifier=user, password="WrongPass123!", client_type="mobile")

        self.assertFalse(unknown.get("ok"), unknown)
        self.assertFalse(wrong.get("ok"), wrong)
        self.assertEqual(unknown.get("error"), "INVALID_CREDENTIALS")
        self.assertEqual(wrong.get("error"), "INVALID_CREDENTIALS")
        self.assertEqual(unknown.get("message"), wrong.get("message"))
        self.assertNotIn("sid", str(unknown))
        self.assertNotIn("password", str(wrong).lower())

    def test_login_rejects_old_identifier_aliases_and_missing_client_type(self):
        user = self.make_user("strict-contract")

        with (
            patch("aos.api.auth.session._rate_limit_login", return_value=None),
            patch("aos.api.auth.session.check_password", return_value=None),
        ):
            alias_response = login_impl(email=user, password="StrongPass123!", client_type="mobile")
            missing_client_response = login_impl(identifier=user, password="StrongPass123!")

        self.assertFalse(alias_response.get("ok"), alias_response)
        self.assertEqual(alias_response.get("error"), "VALIDATION_ERROR")
        self.assertEqual(alias_response.get("data", {}).get("field"), "identifier")
        self.assertFalse(missing_client_response.get("ok"), missing_client_response)
        self.assertEqual(missing_client_response.get("error"), "VALIDATION_ERROR")
        self.assertEqual(missing_client_response.get("data", {}).get("field"), "client_type")

    def test_login_success_mobile_returns_nested_session_sid(self):
        user = self.make_user("login-success")
        login_manager = self._install_fake_login_manager(sid="sid-mobile")

        with (
            patch("aos.api.auth.session._rate_limit_login", return_value=None),
            patch("aos.api.auth.session.check_password", return_value=None),
        ):
            response = login_impl(identifier=user, password="StrongPass123!", client_type="mobile")

        self.assertTrue(response.get("ok"), response)
        data = response.get("data", {})
        self.assertEqual(data.get("session", {}).get("sid"), "sid-mobile")
        self.assertEqual(data.get("user", {}).get("email"), user)
        self.assertIn("preferences", data)
        login_manager.authenticate.assert_called_once()
        login_manager.post_login.assert_called_once()

    def test_login_success_web_omits_sid_from_json(self):
        user = self.make_user("login-web")
        self._install_fake_login_manager(sid="sid-web")

        with (
            patch("aos.api.auth.session._rate_limit_login", return_value=None),
            patch("aos.api.auth.session.check_password", return_value=None),
        ):
            response = login_impl(identifier=user, password="StrongPass123!", client_type="web")

        self.assertTrue(response.get("ok"), response)
        self.assertNotIn("sid", response.get("data", {}).get("session", {}))

    def test_disabled_user_cannot_login_even_with_correct_password(self):
        user = self.make_user("disabled-login", enabled=0)

        with (
            patch("aos.api.auth.session._rate_limit_login", return_value=None),
            patch("aos.api.auth.session.check_password", return_value=None),
        ):
            response = login_impl(identifier=user, password="StrongPass123!", client_type="mobile")

        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "EMAIL_NOT_VERIFIED")

    def test_login_rate_limit_returns_stable_rate_limit_error(self):
        limited = fail("Too many login attempts. Please try again later.", error="RATE_LIMIT")

        with patch("aos.api.auth.session._rate_limit_login", return_value=limited):
            response = login_impl(identifier="user@example.com", password="StrongPass123!", client_type="mobile")

        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "RATE_LIMIT")

    def test_logout_is_idempotent_for_missing_session(self):
        frappe.set_user("Guest")

        response = logout_impl()

        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response.get("data"), {})
