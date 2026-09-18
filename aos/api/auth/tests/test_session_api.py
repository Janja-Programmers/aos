from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.exceptions import AuthenticationError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.api.auth.serializers import serialize_auth_user
from aos.api.auth.session import login_impl, logout_impl, me_impl
from aos.api.auth.verification import ensure_ver_doc, hash_otp
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


_MISSING_LOGIN_MANAGER = object()
_MISSING_RESPONSE = object()


class TestAuthSessionAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-session")
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
        frappe.db.set_value("AOS Profile", {"user": user}, {
            "account_status": "Deleted",
            "deleted_at": now_datetime(), "restore_deadline": add_to_date(now_datetime(), days=7),
            "purge_status": "Pending",
        })
        frappe.db.commit()

    def _pending(self, user):
        ver = ensure_ver_doc(user, purpose="email_verification")
        ver.otp_password_hash = hash_otp("123456")
        ver.expires_at = add_to_date(now_datetime(), minutes=10)
        ver.is_used = 0
        ver.save(ignore_permissions=True)
        frappe.db.commit()

    def test_login_rejects_unknown_fields_and_invalid_identifier_shape(self):
        with patch("aos.api.auth.session._rate_limit_login", return_value=None):
            unknown = login_impl(email_address="person@example.com", password="StrongPass123!", client_type="mobile")
            extra = login_impl(identifier="person@example.com", password="StrongPass123!", client_type="mobile", remember_me=True)
            username = login_impl(identifier="plain_username", password="StrongPass123!", client_type="mobile")
        self.assertEqual(unknown.get("error"), "AUTH_UNKNOWN_FIELD")
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
        self.assertTrue(mobile_response["data"]["session"]["csrf_token"])
        self.assertEqual(
            set(mobile_response["data"]["preferences"]),
            {"country", "currency", "language", "location"},
        )
        self.assertEqual(
            set(mobile_response["data"]["user"]),
            {
                "account_id",
                "email",
                "display_name",
                "avatar",
                "enabled",
                "account_status",
                "is_verified",
            },
        )
        self.assertIn("is_seller", mobile_response["data"]["seller"])
        manager.authenticate.assert_called_once_with(user=mobile, pwd="StrongPass123!")

        web = self.make_user("web")
        self._manager(sid="sid-web")
        with patch("aos.api.auth.session._rate_limit_login", return_value=None):
            web_response = login_impl(identifier=web, password="StrongPass123!", client_type="web")
        self.assertTrue(web_response.get("ok"), web_response)
        self.assertNotIn("sid", web_response["data"]["session"])
        self.assertTrue(web_response["data"]["session"]["csrf_token"])

    def test_enabled_user_missing_profile_is_bootstrap_failure_not_disabled(self):
        user = self.make_user("missing-profile")
        frappe.db.delete("AOS User Preference", {"user": user})
        frappe.db.delete("AOS Profile", {"user": user})
        frappe.db.commit()
        self._manager()
        with patch("aos.api.auth.session._rate_limit_login", return_value=None):
            response = login_impl(identifier=user, password="StrongPass123!", client_type="mobile")
        self.assertEqual(response.get("error"), "ACCOUNT_BOOTSTRAP_UNAVAILABLE")

    def test_me_is_read_only_and_missing_preference_is_invariant_failure(self):
        user = self.make_user("missing-pref", with_preference=False)
        frappe.set_user(user)
        with patch("frappe.db.commit") as commit:
            response = me_impl()
        self.assertEqual(response.get("error"), "ACCOUNT_BOOTSTRAP_UNAVAILABLE")
        self.assertFalse(frappe.db.exists("AOS User Preference", {"user": user}))
        commit.assert_not_called()

    def test_optional_avatar_resolution_failure_does_not_break_auth_bootstrap(self):
        row = SimpleNamespace(
            account_id="ACC-TEST",
            email="avatar@example.com",
            display_name="Avatar User",
            profile_image_media="MEDIA-00000000000000000000000000000001",
            enabled=1,
            account_status="Active",
            is_verified=0,
        )
        with (
            patch("aos.api.auth.serializers.frappe.db.sql", return_value=[row]),
            patch("aos.api.auth.serializers.MediaService.get_public_url", side_effect=RuntimeError("storage down")),
            patch("aos.api.auth.serializers.log_auth_exception") as logged,
        ):
            payload = serialize_auth_user("avatar@example.com")
        self.assertIsNone(payload["avatar"])
        self.assertEqual(payload["account_id"], "ACC-TEST")
        logged.assert_called_once()

    def test_me_guest_and_logout_idempotency(self):
        frappe.set_user("Guest")
        self.assertEqual(me_impl().get("error"), "SESSION_INVALID")
        response = logout_impl()
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response.get("message"), "Already logged out.")
