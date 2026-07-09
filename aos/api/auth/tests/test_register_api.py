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
        frappe.set_user("Guest")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_register_rejects_structured_string_inputs(self):
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
        self.assertTrue(frappe.db.exists("AOS Email Verification", {"user": email, "purpose": "email_verification"}))
        send_email.assert_called_once()
