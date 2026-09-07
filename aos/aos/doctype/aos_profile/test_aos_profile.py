# Copyright (c) 2026, Africa Online Stores and Contributors
# See license.txt

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from aos.services.accounts.identity import normalize_public_account_id
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestAOSProfile(AOSFeatureTestMixin, IntegrationTestCase):
    """Integration coverage for the canonical AOS account identity model."""

    def setUp(self):
        self.prefix = self.make_prefix("aos-profile")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_profile_name_is_opaque_account_id_and_user_is_the_auth_link(self):
        user = self.make_user("identity")
        name = frappe.db.get_value("AOS Profile", {"user": user}, "name")
        self.assertEqual(normalize_public_account_id(name), name)
        self.assertTrue(str(name).startswith("ACC-"))
        self.assertNotEqual(name, user)

        profile = frappe.get_doc("AOS Profile", name)
        self.assertEqual(profile.user, user)
        self.assertTrue(str(profile.display_name or "").strip())

    def test_retired_duplicate_fields_are_not_in_profile_schema(self):
        meta = frappe.get_meta("AOS Profile")
        for field in ("public_id", "is_deleted", "location", "verified_by", "verified_on", "deactivated_at"):
            self.assertFalse(meta.has_field(field), field)
        self.assertTrue(meta.get_field("user").unique)
