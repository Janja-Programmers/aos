from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.api.auth.apple_login import apple_login_impl
from aos.api.auth.google_login import google_login_impl
from aos.api.shared.responses import fail
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAuthSocialLoginAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-social")
        self.created_users: list[str] = []
        self._original_login_manager = getattr(frappe.local, "login_manager", None)
        frappe.local.response = {}
        frappe.set_user("Administrator")

    def tearDown(self):
        if self._original_login_manager is not None:
            frappe.local.login_manager = self._original_login_manager
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _install_fake_login_manager(self, *, sid: str = "sid-social-test"):
        def _login_as(user: str, *_args, **_kwargs):
            frappe.set_user(user)
            frappe.session.sid = sid

        frappe.local.login_manager = SimpleNamespace(
            login_as=Mock(side_effect=_login_as),
            logout=Mock(side_effect=lambda *_args, **_kwargs: frappe.set_user("Guest")),
        )
        return frappe.local.login_manager

    def _bootstrap_kwargs(self) -> dict[str, str]:
        country, language, currency = self.preference_defaults()
        return {"country": country, "language": language, "currency": currency}

    def _google_claims(self, email: str) -> dict[str, str]:
        return {"email": email, "name": "Google Social User", "given_name": "Google"}

    def _apple_claims(self, email: str) -> dict[str, str]:
        return {"email": email, "sub": f"apple-sub-{email}"}

    def _patch_google_success(self, email: str):
        return (
            patch("aos.api.auth.google_login.rate_limit", return_value=None),
            patch("aos.api.auth.google_login._get_google_client_ids", return_value=["test-google-client"]),
            patch("aos.api.auth.google_login.verify_google_id_token", return_value=self._google_claims(email)),
        )

    def _patch_apple_success(self, email: str):
        return (
            patch("aos.api.auth.apple_login.rate_limit", return_value=None),
            patch("aos.api.auth.apple_login._get_apple_audiences", return_value=["com.aos.ios", "com.aos.web"]),
            patch("aos.api.auth.apple_login.verify_apple_id_token", return_value=self._apple_claims(email)),
        )

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

    def _mark_suspended(self, user: str):
        frappe.db.set_value("AOS Profile", user, "account_status", "Suspended")
        frappe.db.commit()

    def test_google_login_rejects_structured_inputs(self):
        response = google_login_impl(id_token={"token": "x"}, client_type="mobile")
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "VALIDATION_ERROR")
        self.assertEqual(response.get("data", {}).get("field"), "id_token")

        response = google_login_impl(id_token="token", client_type=["mobile"])
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("data", {}).get("field"), "client_type")

        response = google_login_impl(id_token="token", client_type="mobile", country={"name": "Kenya"})
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("data", {}).get("field"), "country")

    def test_apple_login_rejects_structured_inputs(self):
        response = apple_login_impl(id_token=["token"], client_type="mobile")
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "VALIDATION_ERROR")
        self.assertEqual(response.get("data", {}).get("field"), "id_token")

        response = apple_login_impl(id_token="token", client_type={"type": "mobile"})
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("data", {}).get("field"), "client_type")

        response = apple_login_impl(id_token="token", client_type="mobile", language=["en"])
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("data", {}).get("field"), "language")

    def test_google_new_user_bootstrap_rolls_back_if_preference_fails(self):
        email = f"{self.prefix}-google-pref-fail@example.com"
        login_manager = self._install_fake_login_manager()

        with self._patch_google_success(email)[0], self._patch_google_success(email)[1], self._patch_google_success(email)[2], patch(
            "aos.api.auth.google_login.ensure_user_preference",
            return_value=(None, fail("Default country not configured.", error="CONFIG_ERROR")),
        ):
            response = google_login_impl(id_token="google-token", client_type="mobile", **self._bootstrap_kwargs())

        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "CONFIG_ERROR")
        self.assertFalse(frappe.db.exists("User", email))
        self.assertFalse(frappe.db.exists("AOS Profile", email))
        self.assertFalse(frappe.db.exists("AOS User Preference", {"user": email}))
        login_manager.login_as.assert_not_called()

    def test_apple_new_user_bootstrap_rolls_back_if_preference_fails(self):
        email = f"{self.prefix}-apple-pref-fail@example.com"
        login_manager = self._install_fake_login_manager()

        with self._patch_apple_success(email)[0], self._patch_apple_success(email)[1], self._patch_apple_success(email)[2], patch(
            "aos.api.auth.apple_login.ensure_user_preference",
            return_value=(None, fail("Default country not configured.", error="CONFIG_ERROR")),
        ):
            response = apple_login_impl(id_token="apple-token", client_type="mobile", **self._bootstrap_kwargs())

        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "CONFIG_ERROR")
        self.assertFalse(frappe.db.exists("User", email))
        self.assertFalse(frappe.db.exists("AOS Profile", email))
        self.assertFalse(frappe.db.exists("AOS User Preference", {"user": email}))
        login_manager.login_as.assert_not_called()

    def test_existing_user_is_not_deleted_when_preference_sync_fails(self):
        google_user = self.make_user("existing-google", with_preference=False)
        apple_user = self.make_user("existing-apple", with_preference=False)
        login_manager = self._install_fake_login_manager()

        with self._patch_google_success(google_user)[0], self._patch_google_success(google_user)[1], self._patch_google_success(google_user)[2], patch(
            "aos.api.auth.google_login.ensure_user_preference",
            return_value=(None, fail("Default country not configured.", error="CONFIG_ERROR")),
        ):
            google_response = google_login_impl(id_token="google-token", client_type="mobile")

        with self._patch_apple_success(apple_user)[0], self._patch_apple_success(apple_user)[1], self._patch_apple_success(apple_user)[2], patch(
            "aos.api.auth.apple_login.ensure_user_preference",
            return_value=(None, fail("Default country not configured.", error="CONFIG_ERROR")),
        ):
            apple_response = apple_login_impl(id_token="apple-token", client_type="mobile")

        for response in (google_response, apple_response):
            self.assertFalse(response.get("ok"), response)
            self.assertEqual(response.get("error"), "CONFIG_ERROR")

        for user in (google_user, apple_user):
            self.assertTrue(frappe.db.exists("User", user))
            self.assertTrue(frappe.db.exists("AOS Profile", user))

        login_manager.login_as.assert_not_called()

    def test_existing_disabled_suspended_deleted_users_are_not_reenabled(self):
        disabled_user = self.make_user("disabled", enabled=0)
        suspended_user = self.make_user("suspended")
        deleted_user = self.make_user("deleted")
        self._mark_suspended(suspended_user)
        self._mark_deleted(deleted_user)

        with self._patch_google_success(disabled_user)[0], self._patch_google_success(disabled_user)[1], self._patch_google_success(disabled_user)[2]:
            disabled_response = google_login_impl(id_token="google-token", client_type="mobile")

        with self._patch_apple_success(suspended_user)[0], self._patch_apple_success(suspended_user)[1], self._patch_apple_success(suspended_user)[2]:
            suspended_response = apple_login_impl(id_token="apple-token", client_type="mobile")

        with self._patch_google_success(deleted_user)[0], self._patch_google_success(deleted_user)[1], self._patch_google_success(deleted_user)[2]:
            deleted_response = google_login_impl(id_token="google-token", client_type="mobile")

        self.assertEqual(disabled_response.get("error"), "ACCOUNT_DISABLED")
        self.assertEqual(suspended_response.get("error"), "ACCOUNT_SUSPENDED")
        self.assertIn(deleted_response.get("error"), {"ACCOUNT_DELETED", "ACCOUNT_DELETED_RESTORABLE"})
        self.assertEqual(int(frappe.db.get_value("User", disabled_user, "enabled") or 0), 0)
        self.assertEqual(frappe.db.get_value("AOS Profile", suspended_user, "account_status"), "Suspended")
        self.assertEqual(frappe.db.get_value("AOS Profile", deleted_user, "account_status"), "Deleted")

    def test_google_and_apple_mobile_response_follow_session_contract(self):
        google_email = f"{self.prefix}-google-mobile@example.com"
        apple_email = f"{self.prefix}-apple-mobile@example.com"
        self._install_fake_login_manager(sid="sid-mobile-social")

        with self._patch_google_success(google_email)[0], self._patch_google_success(google_email)[1], self._patch_google_success(google_email)[2]:
            google_response = google_login_impl(id_token="google-token", client_type="mobile", **self._bootstrap_kwargs())

        with self._patch_apple_success(apple_email)[0], self._patch_apple_success(apple_email)[1], self._patch_apple_success(apple_email)[2]:
            apple_response = apple_login_impl(id_token="apple-token", client_type="mobile", **self._bootstrap_kwargs())

        for email, response in ((google_email, google_response), (apple_email, apple_response)):
            self.created_users.append(email)
            self.assertTrue(response.get("ok"), response)
            self.assertEqual(response.get("data", {}).get("session", {}).get("sid"), "sid-mobile-social")
            self.assertEqual(response.get("data", {}).get("user", {}).get("email"), email)
            self.assertTrue(frappe.db.exists("User", email))
            self.assertTrue(frappe.db.exists("AOS Profile", email))
            self.assertTrue(frappe.db.exists("AOS User Preference", {"user": email}))

    def test_google_and_apple_signup_resolve_missing_preferences_from_request_context(self):
        google_email = f"{self.prefix}-google-context@example.com"
        apple_email = f"{self.prefix}-apple-context@example.com"
        country, language, currency = self.preference_defaults()
        self._install_fake_login_manager()
        header_values = {"X-Country-Code": frappe.db.get_value("Country", country, "code") or "", "Accept-Language": frappe.db.get_value("Language", language, "language_code") or ""}
        with patch("aos.api.localization.context._header", side_effect=lambda name: header_values.get(name, "")):
            with self._patch_google_success(google_email)[0], self._patch_google_success(google_email)[1], self._patch_google_success(google_email)[2]:
                google_response = google_login_impl(id_token="google-token", client_type="mobile", currency=currency)
            frappe.set_user("Administrator")
            with self._patch_apple_success(apple_email)[0], self._patch_apple_success(apple_email)[1], self._patch_apple_success(apple_email)[2]:
                apple_response = apple_login_impl(id_token="apple-token", client_type="mobile", currency=currency)
        for email, response in ((google_email, google_response), (apple_email, apple_response)):
            self.created_users.append(email)
            self.assertTrue(response.get("ok"), response)
            preference = frappe.db.get_value("AOS User Preference", {"user": email}, ["country", "currency", "language"], as_dict=True)
            self.assertEqual((preference.country, preference.currency, preference.language), (country, currency, language))

    def test_google_and_apple_web_response_omits_sid(self):
        google_email = f"{self.prefix}-google-web@example.com"
        apple_email = f"{self.prefix}-apple-web@example.com"
        self._install_fake_login_manager(sid="sid-web-social")

        with self._patch_google_success(google_email)[0], self._patch_google_success(google_email)[1], self._patch_google_success(google_email)[2]:
            google_response = google_login_impl(id_token="google-token", client_type="web", **self._bootstrap_kwargs())

        with self._patch_apple_success(apple_email)[0], self._patch_apple_success(apple_email)[1], self._patch_apple_success(apple_email)[2]:
            apple_response = apple_login_impl(id_token="apple-token", client_type="web", **self._bootstrap_kwargs())

        for email, response in ((google_email, google_response), (apple_email, apple_response)):
            self.created_users.append(email)
            self.assertTrue(response.get("ok"), response)
            self.assertNotIn("sid", response.get("data", {}).get("session", {}))

    def test_social_config_errors_use_http_503(self):
        frappe.local.response = {}
        with patch("aos.api.auth.google_login.rate_limit", return_value=None), patch(
            "aos.api.auth.google_login._get_google_client_ids",
            return_value=[],
        ):
            google_response = google_login_impl(id_token="google-token", client_type="mobile")
        google_status = frappe.local.response.get("http_status_code")

        frappe.local.response = {}
        with patch("aos.api.auth.apple_login.rate_limit", return_value=None), patch(
            "aos.api.auth.apple_login._get_apple_audiences",
            return_value=[],
        ):
            apple_response = apple_login_impl(id_token="apple-token", client_type="mobile")
        apple_status = frappe.local.response.get("http_status_code")

        self.assertEqual(google_response.get("error"), "CONFIG_ERROR")
        self.assertEqual(apple_response.get("error"), "CONFIG_ERROR")
        self.assertEqual(google_status, 503)
        self.assertEqual(apple_status, 503)
