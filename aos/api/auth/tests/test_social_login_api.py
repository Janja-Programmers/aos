from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.auth.apple_login import apple_login_impl
from aos.api.auth.google_login import google_login_impl
from aos.api.auth.social_identity import get_bound_user
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAuthSocialLoginAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-social")
        self.created_users: list[str] = []
        self.original_manager = getattr(frappe.local, "login_manager", None)
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()
        self._manager()

    def tearDown(self):
        if self.original_manager is not None:
            frappe.local.login_manager = self.original_manager
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _manager(self, sid="social-sid"):
        def login_as(_user):
            frappe.session.sid = sid
        frappe.local.login_manager = SimpleNamespace(login_as=Mock(side_effect=login_as))

    def _bootstrap(self):
        c, l, cur = self.preference_defaults()
        return {"country": c, "language": l, "currency": cur}

    def _google_claims(self, email, subject="google-sub"):
        return {"sub": subject, "email": email, "email_verified": True, "name": "Google User"}

    def _apple_claims(self, email, subject="apple-sub"):
        return {"sub": subject, "email": email}

    def test_google_new_user_creates_durable_subject_binding(self):
        email = f"{self.prefix}-google@example.com"
        with patch("aos.api.auth.google_login.get_google_oauth_client_ids", return_value=["google-client"]), patch(
            "aos.api.auth.google_login.verify_google_id_token", return_value=self._google_claims(email, "google-123")
        ), patch("aos.api.auth.social_login.auth_ip_limit", return_value=None), patch("aos.api.auth.social_login.auth_rate_limit", return_value=None):
            response = google_login_impl(id_token="token", client_type="mobile", **self._bootstrap())
        self.created_users.append(email)
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(get_bound_user("google", "google-123"), email)
        self.assertEqual(response["data"]["session"]["sid"], "social-sid")

    def test_apple_subject_binding_allows_later_token_without_email(self):
        email = f"{self.prefix}-apple@example.com"
        with patch("aos.api.auth.apple_login.get_apple_oauth_client_ids", return_value=["apple-client"]), patch(
            "aos.api.auth.apple_login.verify_apple_id_token", return_value=self._apple_claims(email, "apple-123")
        ), patch("aos.api.auth.social_login.auth_ip_limit", return_value=None), patch("aos.api.auth.social_login.auth_rate_limit", return_value=None):
            first = apple_login_impl(id_token="first", client_type="web", **self._bootstrap())
        self.created_users.append(email)
        self.assertTrue(first.get("ok"), first)
        with patch("aos.api.auth.apple_login.get_apple_oauth_client_ids", return_value=["apple-client"]), patch(
            "aos.api.auth.apple_login.verify_apple_id_token", return_value={"sub": "apple-123"}
        ), patch("aos.api.auth.social_login.auth_ip_limit", return_value=None), patch("aos.api.auth.social_login.auth_rate_limit", return_value=None):
            second = apple_login_impl(id_token="later", client_type="web")
        self.assertTrue(second.get("ok"), second)
        self.assertNotIn("sid", second["data"]["session"])

    def test_unbound_apple_token_without_email_is_rejected(self):
        with patch("aos.api.auth.apple_login.get_apple_oauth_client_ids", return_value=["apple-client"]), patch(
            "aos.api.auth.apple_login.verify_apple_id_token", return_value={"sub": "new-sub-no-email"}
        ), patch("aos.api.auth.social_login.auth_ip_limit", return_value=None), patch("aos.api.auth.social_login.auth_rate_limit", return_value=None):
            response = apple_login_impl(id_token="token", client_type="web")
        self.assertEqual(response.get("error"), "TOKEN_INVALID")

    def test_existing_user_missing_localization_is_not_repaired_on_social_login(self):
        email = self.make_user("existing-no-pref", with_preference=False)
        with patch("aos.api.auth.google_login.get_google_oauth_client_ids", return_value=["google-client"]), patch(
            "aos.api.auth.google_login.verify_google_id_token", return_value=self._google_claims(email, "existing-sub")
        ), patch("aos.api.auth.social_login.auth_ip_limit", return_value=None), patch("aos.api.auth.social_login.auth_rate_limit", return_value=None):
            response = google_login_impl(id_token="token", client_type="web")
        self.assertEqual(response.get("error"), "ACCOUNT_BOOTSTRAP_UNAVAILABLE")
        self.assertFalse(frappe.db.exists("AOS User Preference", {"user": email}))

    def test_provider_subject_cannot_silently_rebind_to_another_account(self):
        email1 = f"{self.prefix}-one@example.com"
        email2 = f"{self.prefix}-two@example.com"
        common = [patch("aos.api.auth.social_login.auth_ip_limit", return_value=None), patch("aos.api.auth.social_login.auth_rate_limit", return_value=None)]
        with patch("aos.api.auth.google_login.get_google_oauth_client_ids", return_value=["google-client"]), patch(
            "aos.api.auth.google_login.verify_google_id_token", return_value=self._google_claims(email1, "stable-sub")
        ), common[0], common[1]:
            first = google_login_impl(id_token="token1", client_type="web", **self._bootstrap())
        self.created_users.append(email1)
        self.assertTrue(first.get("ok"), first)
        # The same subject stays bound to email1 even if a later token's email differs.
        with patch("aos.api.auth.google_login.get_google_oauth_client_ids", return_value=["google-client"]), patch(
            "aos.api.auth.google_login.verify_google_id_token", return_value=self._google_claims(email2, "stable-sub")
        ), patch("aos.api.auth.social_login.auth_ip_limit", return_value=None), patch("aos.api.auth.social_login.auth_rate_limit", return_value=None):
            second = google_login_impl(id_token="token2", client_type="web")
        self.assertTrue(second.get("ok"), second)
        self.assertFalse(frappe.db.exists("User", email2))
        self.assertEqual(get_bound_user("google", "stable-sub"), email1)

    def test_unknown_legacy_fields_are_rejected(self):
        response = google_login_impl(id_token="token", client_type="web", access_token="legacy")
        self.assertEqual(response.get("error"), "AUTH_UNKNOWN_FIELD")

    def test_apple_present_email_must_be_provider_verified(self):
        from aos.api.auth.apple_jwt import verify_apple_id_token
        from aos.api.auth.oidc import OIDCTokenError

        with patch("aos.api.auth.apple_jwt.verify_rs256_token", return_value={"sub": "a", "email": "person@example.com", "email_verified": False}):
            with self.assertRaises(OIDCTokenError):
                verify_apple_id_token("token", ["client"])
