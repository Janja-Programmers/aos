from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.auth.register import register_impl
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAuthRegisterAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-register")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()
        frappe.set_user("Guest")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_register_rejects_structured_string_inputs(self):
        # This test verifies the validator contract, not Redis rate-limit state.
        # The full application suite intentionally reuses one request IP, so an
        # earlier registration test must not mask the field-specific failures.
        with patch("aos.api.auth.register.rate_limit", return_value=None):
            response = register_impl(email={"value": "x@example.com"}, full_name="Example User", password="StrongPass123!")
            self.assertFalse(response.get("ok"), response)
            self.assertEqual(response.get("error"), "VALIDATION_ERROR")
            self.assertEqual(response.get("data", {}).get("field"), "email")

            response = register_impl(email=f"{self.prefix}-x@example.com", full_name=["Example"], password="StrongPass123!")
            self.assertFalse(response.get("ok"), response)
            self.assertEqual(response.get("data", {}).get("field"), "full_name")

            response = register_impl(email=f"{self.prefix}-x@example.com", full_name="Example User", password={"secret": "StrongPass123!"})
            self.assertFalse(response.get("ok"), response)
            self.assertEqual(response.get("data", {}).get("field"), "password")

    def test_register_rejects_structured_optional_bootstrap_inputs(self):
        with patch("aos.api.auth.register.rate_limit", return_value=None):
            response = register_impl(
                email=f"{self.prefix}-x@example.com",
                full_name="Example User",
                password="StrongPass123!",
                country={"name": "Kenya"},
            )
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "VALIDATION_ERROR")
        self.assertEqual(response.get("data", {}).get("field"), "country")

    def test_register_success_creates_durable_auth_rows_before_email(self):
        email = f"{self.prefix}-signup@example.com"
        country, language, currency = self.preference_defaults()

        with (
            patch("aos.api.auth.register.rate_limit", return_value=None),
            patch("aos.api.auth.register.generate_otp", return_value="123456"),
            patch("aos.api.auth.register.send_otp_email") as send_email,
        ):
            response = register_impl(
                email=email,
                full_name="Example Signup",
                password="StrongPass123!",
                country=country,
                language=language,
                currency=currency,
            )

        self.created_users.append(email)
        self.assertTrue(response.get("ok"), response)
        self.assertTrue(frappe.db.exists("User", email))
        self.assertTrue(frappe.db.exists("AOS Profile", email))
        self.assertTrue(frappe.db.exists("AOS User Preference", {"user": email}))
        preference = frappe.db.get_value("AOS User Preference", {"user": email}, ["country", "currency", "language"], as_dict=True)
        self.assertEqual(preference.country, country)
        self.assertEqual(preference.currency, currency)
        self.assertEqual(preference.language, language)
        self.assertTrue(frappe.db.exists("AOS Email Verification", {"user": email, "purpose": "email_verification"}))
        send_email.assert_called_once()

    def test_register_missing_preferences_uses_settings_defaults(self):
        email = f"{self.prefix}-defaults@example.com"
        country, language, currency = self.preference_defaults()
        with (
            patch("aos.api.auth.register.rate_limit", return_value=None),
            patch("aos.api.auth.register.generate_otp", return_value="123456"),
            patch("aos.api.auth.register.send_otp_email"),
        ):
            response = register_impl(email=email, full_name="Default Signup", password="StrongPass123!")
        self.created_users.append(email)
        self.assertTrue(response.get("ok"), response)
        preference = frappe.db.get_value("AOS User Preference", {"user": email}, ["country", "currency", "language"], as_dict=True)
        self.assertEqual((preference.country, preference.language, preference.currency), (country, language, currency))

    def test_register_missing_language_uses_accept_language(self):
        email = f"{self.prefix}-header-language@example.com"
        country, _default_language, currency = self.preference_defaults()
        filters = {"enabled": 1} if frappe.get_meta("Language").has_field("enabled") else {}
        language = frappe.db.get_value("Language", filters, ["name", "language_code"], as_dict=True)
        if not language or not language.language_code:
            self.skipTest("An enabled language code is required")
        with (
            patch(
                "aos.api.auth.account_helpers.accept_language_hint",
                return_value=f"{language.language_code}-XX,{language.language_code};q=0.9",
            ),
            patch("aos.api.auth.register.rate_limit", return_value=None),
            patch("aos.api.auth.register.generate_otp", return_value="123456"),
            patch("aos.api.auth.register.send_otp_email"),
        ):
            response = register_impl(email=email, full_name="Header Language", password="StrongPass123!", country=country, currency=currency)
        self.created_users.append(email)
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(frappe.db.get_value("AOS User Preference", {"user": email}, "language"), language.name)

    def test_register_missing_country_uses_valid_country_header(self):
        email = f"{self.prefix}-header-country@example.com"
        _country, language, currency = self.preference_defaults()
        country = frappe.db.get_value("Country", {"code": ["is", "set"]}, ["name", "code"], as_dict=True)
        if not country:
            self.skipTest("A coded country is required")
        with (
            patch("aos.api.auth.account_helpers.geo_country_hint", return_value=country.code),
            patch("aos.api.auth.register.rate_limit", return_value=None),
            patch("aos.api.auth.register.generate_otp", return_value="123456"),
            patch("aos.api.auth.register.send_otp_email"),
        ):
            response = register_impl(email=email, full_name="Header Country", password="StrongPass123!", language=language, currency=currency)
        self.created_users.append(email)
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(frappe.db.get_value("AOS User Preference", {"user": email}, "country"), country.name)
