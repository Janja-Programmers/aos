from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.exceptions import AuthenticationError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.api.auth.session import login_impl, logout_impl, me_impl
from aos.api.auth.verification import ensure_ver_doc, hash_otp
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

    def _manager(self, *, sid="sid-test", auth_error=False):
        def post_login():
            frappe.session.sid = sid
        authenticate = Mock(side_effect=AuthenticationError if auth_error else None)
        frappe.local.login_manager = SimpleNamespace(
            authenticate=authenticate,
            post_login=Mock(side_effect=post_login),
            logout=Mock(side_effect=lambda: frappe.set_user("Guest")),
        )
        return frappe.local.login_manager

    def _deleted(self, user):
        frappe.db.set_value("AOS Profile", user, {
            "account_status": "Deleted", "is_deleted": 1,
            "deleted_at": now_datetime(), "restore_deadline": add_to_date(now_datetime(), days=7),
        })
        frappe.db.commit()

    def _pending(self, user):
        ver = ensure_ver_doc(user, email=user, purpose="email_verification")
        ver.otp_password_hash = hash_otp("123456")
        ver.expires_at = add_to_date(now_datetime(), minutes=10)
        ver.is_used = 0
        ver.save(ignore_permissions=True)
        frappe.db.commit()

    def test_login_rejects_legacy_aliases_unknown_fields_and_username_shape(self):
        with patch("aos.api.auth.session._rate_limit_login", return_value=None):
            alias = login_impl(email="person@example.com", password="StrongPass123!", client_type="mobile")
            extra = login_impl(identifier="person@example.com", password="StrongPass123!", client_type="mobile", remember_me=True)
            username = login_impl(identifier="plain_username", password="StrongPass123!", client_type="mobile")
        self.assertEqual(alias.get("error"), "AUTH_UNKNOWN_FIELD")
        self.assertEqual(extra.get("error"), "AUTH_UNKNOWN_FIELD")
        self.assertEqual(username.get("error"), "VALIDATION_ERROR")

    def test_unknown_and_wrong_password_share_generic_contract(self):
        user = self.make_user("wrong")
        self._manager(auth_error=True)
        with patch("aos.api.auth.session._rate_limit_login", return_value=None), patch("aos.api.auth.session._dummy_password_work"):
            unknown = login_impl(identifier=f"{self.prefix}-missing@example.com", password="WrongPass123!", client_type="mobile")
            wrong = login_impl(identifier=user, password="WrongPass123!", client_type="mobile")
        for response in (unknown, wrong):
            self.assertEqual(response.get("error"), "INVALID_CREDENTIALS")
            self.assertEqual(response.get("message"), "Invalid credentials.")

    def test_deleted_state_is_only_exposed_after_password_proof(self):
        user = self.make_user("deleted")
        self._deleted(user)
        self._manager()
        with patch("aos.api.auth.session._rate_limit_login", return_value=None):
            response = login_impl(identifier=user, password="StrongPass123!", client_type="mobile")
        self.assertIn(response.get("error"), {"ACCOUNT_DELETED", "ACCOUNT_DELETED_RESTORABLE"})

    def test_disabled_pending_and_disabled_admin_accounts(self):
        pending = self.make_user("pending", enabled=0)
        disabled = self.make_user("disabled", enabled=0)
        self._pending(pending)
        with patch("aos.api.auth.session._rate_limit_login", return_value=None), patch("aos.api.auth.session.check_password", return_value=None):
            pending_response = login_impl(identifier=pending, password="StrongPass123!", client_type="mobile")
            disabled_response = login_impl(identifier=disabled, password="StrongPass123!", client_type="mobile")
        self.assertEqual(pending_response.get("error"), "EMAIL_NOT_VERIFIED")
        self.assertEqual(disabled_response.get("error"), "ACCOUNT_DISABLED")

    def test_mobile_returns_sid_web_omits_sid(self):
        mobile = self.make_user("mobile")
        manager = self._manager(sid="sid-mobile")
        with patch("aos.api.auth.session._rate_limit_login", return_value=None):
            mobile_response = login_impl(identifier=mobile, password="StrongPass123!", client_type="mobile")
        self.assertTrue(mobile_response.get("ok"), mobile_response)
        self.assertEqual(mobile_response["data"]["session"]["sid"], "sid-mobile")
        manager.authenticate.assert_called_once_with(user=mobile, pwd="StrongPass123!")

        web = self.make_user("web")
        self._manager(sid="sid-web")
        with patch("aos.api.auth.session._rate_limit_login", return_value=None):
            web_response = login_impl(identifier=web, password="StrongPass123!", client_type="web")
        self.assertTrue(web_response.get("ok"), web_response)
        self.assertNotIn("sid", web_response["data"]["session"])

    def test_me_is_read_only_and_missing_preference_is_invariant_failure(self):
        user = self.make_user("missing-pref", with_preference=False)
        frappe.set_user(user)
        with patch("frappe.db.commit") as commit:
            response = me_impl()
        self.assertEqual(response.get("error"), "ACCOUNT_BOOTSTRAP_UNAVAILABLE")
        self.assertFalse(frappe.db.exists("AOS User Preference", {"user": user}))
        commit.assert_not_called()

    def test_me_guest_and_logout_idempotency(self):
        frappe.set_user("Guest")
        self.assertEqual(me_impl().get("error"), "SESSION_INVALID")
        response = logout_impl()
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response.get("message"), "Already logged out.")
