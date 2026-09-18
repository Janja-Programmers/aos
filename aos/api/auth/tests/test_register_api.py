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

    def _register(self, email, **extra):
        with patch("aos.api.auth.register.auth_rate_limit", return_value=None), patch("aos.api.auth.register.auth_ip_limit", return_value=None), patch("aos.api.auth.otp_service.queue_otp_email") as queue:
            response = register_impl(email=email, full_name="Example User", password="StrongPass123!", **extra)
        return response, queue

    def test_registration_contract_rejects_structured_and_unknown_inputs(self):
        with patch("aos.api.auth.register.auth_rate_limit", return_value=None), patch("aos.api.auth.register.auth_ip_limit", return_value=None):
            structured = register_impl(email={"value": "x@example.com"}, full_name="Example", password="StrongPass123!")
            response = register_impl(email="x@example.com", full_name="Example", password="StrongPass123!", unexpected="x")
        self.assertEqual(structured.get("error"), "VALIDATION_ERROR")
        self.assertEqual(response.get("error"), "AUTH_UNKNOWN_FIELD")

    def test_success_creates_user_profile_localization_and_slow_otp_state(self):
        email = f"{self.prefix}-success@example.com"
        country, language, currency = self.preference_defaults()
        response, queue = self._register(email, country=country, language=language, currency=currency)
        self.created_users.append(email)
        self.assertTrue(response.get("ok"), response)
        self.assertTrue(frappe.db.exists("User", email))
        self.assertTrue(frappe.db.exists("AOS Profile", {"user": email}))
        self.assertTrue(frappe.db.exists("AOS User Preference", {"user": email}))
        from aos.api.auth.verification import challenge_name
        name = challenge_name(email, "email_verification")
        stored = frappe.db.get_value("AOS Auth Challenge", name, "otp_password_hash")
        self.assertTrue(stored)
        self.assertNotEqual(stored, "123456")
        queue.assert_called_once()

    def test_duplicate_registration_is_enumeration_safe_idempotent_acceptance(self):
        email = self.make_user("duplicate")
        with patch("aos.api.auth.register.auth_rate_limit", return_value=None), patch(
            "aos.api.auth.register.auth_ip_limit", return_value=None
        ), patch("aos.api.auth.register.dummy_password_write_work") as dummy_password, patch(
            "aos.api.auth.register.dummy_otp_issue_work"
        ) as dummy_otp:
            response = register_impl(email=email, full_name="Duplicate", password="StrongPass123!")
        self.assertTrue(response.get("ok"), response)
        self.assertNotIn("error", response)
        dummy_password.assert_called_once_with("StrongPass123!")
        dummy_otp.assert_called_once_with()

    def test_concurrent_duplicate_insert_race_maps_to_safe_retry_acceptance(self):
        email = f"{self.prefix}-race@example.com"
        with patch("aos.api.auth.register.auth_rate_limit", return_value=None), patch("aos.api.auth.register.auth_ip_limit", return_value=None), patch("frappe.new_doc") as new_doc:
            fake = new_doc.return_value
            fake.insert.side_effect = frappe.DuplicateEntryError
            response = register_impl(email=email, full_name="Race", password="StrongPass123!")
        self.assertTrue(response.get("ok"), response)
        self.assertNotIn("error", response)

    def test_queue_failure_rolls_back_partial_registration(self):
        email = f"{self.prefix}-rollback@example.com"
        with patch("aos.api.auth.register.auth_rate_limit", return_value=None), patch("aos.api.auth.register.auth_ip_limit", return_value=None), patch(
            "aos.api.auth.otp_service.queue_otp_email", side_effect=RuntimeError("mail queue unavailable")
        ):
            response = register_impl(email=email, full_name="Rollback", password="StrongPass123!")
        self.assertEqual(response.get("error"), "REGISTER_FAILED")
        self.assertFalse(frappe.db.exists("User", email))
        self.assertFalse(frappe.db.exists("AOS Profile", {"user": email}))
        self.assertFalse(frappe.db.exists("AOS User Preference", {"user": email}))

    def test_missing_localization_inputs_use_localization_defaults(self):
        email = f"{self.prefix}-defaults@example.com"
        expected = self.preference_defaults()
        response, _ = self._register(email)
        self.created_users.append(email)
        self.assertTrue(response.get("ok"), response)
        pref = frappe.db.get_value("AOS User Preference", {"user": email}, ["country", "language", "currency"], as_dict=True)
        self.assertEqual((pref.country, pref.language, pref.currency), expected)

    def test_password_policy_failure_is_rejected(self):
        email = f"{self.prefix}-weak@example.com"
        with patch("aos.api.auth.register.auth_ip_limit", return_value=None), patch("aos.api.auth.register.auth_rate_limit", return_value=None), patch(
            "aos.api.auth.validators.validate_password_strength", return_value={"ok": False, "error": "VALIDATION_ERROR"}
        ):
            response = register_impl(email=email, full_name="Weak User", password="weakpass")
        self.assertEqual(response.get("error"), "VALIDATION_ERROR")
        self.assertFalse(frappe.db.exists("User", email))
