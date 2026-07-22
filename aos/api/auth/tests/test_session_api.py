from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.exceptions import AuthenticationError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.api.auth.session import login_impl, logout_impl, me_impl
from aos.api.auth.verification import ensure_ver_doc
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAuthSessionAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-session")
        self.created_users: list[str] = []
        self._original_login_manager = getattr(frappe.local, "login_manager", None)
        frappe.local.response = {}
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        if self._original_login_manager is not None:
            frappe.local.login_manager = self._original_login_manager
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _install_fake_login_manager(self, *, sid: str = "sid-test-value"):
        def _post_login(*_args, **_kwargs):
            frappe.session.sid = sid

        frappe.local.login_manager = SimpleNamespace(
            authenticate=Mock(),
            post_login=Mock(side_effect=_post_login),
            logout=Mock(side_effect=lambda *_args, **_kwargs: frappe.set_user("Guest")),
        )
        return frappe.local.login_manager

    def _mark_deleted(self, user: str):
        frappe.db.set_value(
            "AOS Profile",
            user,
            {
                "account_status": "Deleted",
                "is_deleted": 1,
                "deleted_at": now_datetime(),
                "restore_deadline": add_to_date(now_datetime(), days=7),
            },
        )
        frappe.db.commit()

    def _create_pending_email_verification(self, user: str):
        ver = ensure_ver_doc(user, email=user, purpose="email_verification")
        ver.otp_hash = "hash"
        ver.expires_at = add_to_date(now_datetime(), minutes=10)
        ver.is_used = 0
        ver.save(ignore_permissions=True)
        frappe.db.commit()

    def test_login_rejects_non_string_inputs(self):
        response = login_impl(identifier={"email": "x@example.com"}, password="StrongPass123!", client_type="mobile")
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "VALIDATION_ERROR")
        self.assertEqual(response.get("data", {}).get("field"), "identifier")

        response = login_impl(identifier="x@example.com", password=["StrongPass123!"], client_type="mobile")
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "VALIDATION_ERROR")
        self.assertEqual(response.get("data", {}).get("field"), "password")

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

    def test_login_wrong_unknown_and_deleted_wrong_password_are_not_enumerable(self):
        active_user = self.make_user("wrong-password")
        deleted_user = self.make_user("deleted-wrong")
        self._mark_deleted(deleted_user)

        with patch("aos.api.auth.session._rate_limit_login", return_value=None):
            unknown = login_impl(identifier=f"{self.prefix}-missing@example.com", password="WrongPass123!", client_type="mobile")

        with (
            patch("aos.api.auth.session._rate_limit_login", return_value=None),
            patch("aos.api.auth.session.check_password", side_effect=AuthenticationError),
        ):
            wrong = login_impl(identifier=active_user, password="WrongPass123!", client_type="mobile")
            deleted_wrong = login_impl(identifier=deleted_user, password="WrongPass123!", client_type="mobile")

        for response in (unknown, wrong, deleted_wrong):
            self.assertFalse(response.get("ok"), response)
            self.assertEqual(response.get("error"), "INVALID_CREDENTIALS")
            self.assertEqual(response.get("message"), "Invalid credentials.")

        self.assertNotIn(deleted_user, str(deleted_wrong))
        self.assertNotIn("WrongPass123!", str(deleted_wrong))

    def test_deleted_account_state_is_disclosed_only_after_password_proof(self):
        user = self.make_user("deleted-correct")
        self._mark_deleted(user)

        with (
            patch("aos.api.auth.session._rate_limit_login", return_value=None),
            patch("aos.api.auth.session.check_password", return_value=None),
        ):
            response = login_impl(identifier=user, password="StrongPass123!", client_type="mobile")

        self.assertFalse(response.get("ok"), response)
        self.assertIn(response.get("error"), {"ACCOUNT_DELETED", "ACCOUNT_DELETED_RESTORABLE"})
        self.assertIn("can_restore", response.get("data", {}))

    def test_disabled_user_errors_distinguish_pending_verification_from_admin_disabled(self):
        pending = self.make_user("pending-verification", enabled=0)
        self._create_pending_email_verification(pending)
        disabled = self.make_user("admin-disabled", enabled=0)

        with (
            patch("aos.api.auth.session._rate_limit_login", return_value=None),
            patch("aos.api.auth.session.check_password", return_value=None),
        ):
            pending_response = login_impl(identifier=pending, password="StrongPass123!", client_type="mobile")
            disabled_response = login_impl(identifier=disabled, password="StrongPass123!", client_type="mobile")

        self.assertEqual(pending_response.get("error"), "EMAIL_NOT_VERIFIED")
        self.assertEqual(disabled_response.get("error"), "ACCOUNT_DISABLED")

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

    def test_logout_is_idempotent_for_missing_session(self):
        frappe.set_user("Guest")
        response = logout_impl()
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response.get("data"), {})
