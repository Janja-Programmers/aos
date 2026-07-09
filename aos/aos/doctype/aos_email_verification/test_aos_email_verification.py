# Copyright (c) 2026, Africa Online Stores and Contributors
# See license.txt

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from aos.api.auth.verification import ensure_ver_doc
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestAOSEmailVerification(AOSFeatureTestMixin, IntegrationTestCase):
    """Integration tests for AOS Email Verification model invariants."""

    def setUp(self):
        self.prefix = self.make_prefix("aos-email-verification")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_controller_requires_existing_user(self):
        doc = frappe.get_doc(
            {
                "doctype": "AOS Email Verification",
                "user": f"{self.prefix}-missing@example.com",
                "email": f"{self.prefix}-missing@example.com",
                "purpose": "email_verification",
            }
        )
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_controller_rejects_invalid_purpose(self):
        user = self.make_user("invalid-purpose")
        doc = frappe.get_doc(
            {
                "doctype": "AOS Email Verification",
                "user": user,
                "email": user,
                "purpose": "unsupported",
            }
        )
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_ensure_ver_doc_is_idempotent_per_user_purpose(self):
        user = self.make_user("idempotent")
        first = ensure_ver_doc(user, email=user, purpose="password_reset")
        second = ensure_ver_doc(user, email=user, purpose="password_reset")

        self.assertEqual(first.name, second.name)
        self.assertEqual(
            frappe.db.count("AOS Email Verification", {"user": user, "purpose": "password_reset"}),
            1,
        )
