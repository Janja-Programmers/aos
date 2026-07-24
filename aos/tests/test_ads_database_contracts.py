from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.patches.v1_0 import harden_ads_subsystem


class TestAdsDatabaseContracts(FrappeTestCase):
    def test_ads_patch_is_idempotent_and_indexes_exist(self):
        harden_ads_subsystem.execute()
        harden_ads_subsystem.execute()
        for index_name, (doctype, fields) in {
            **harden_ads_subsystem._INDEXES,
            **harden_ads_subsystem._UNIQUES,
        }.items():
            if not harden_ads_subsystem._supports(doctype, fields):
                continue
            rows = frappe.db.sql(
                """
                SELECT INDEX_NAME FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                  AND INDEX_NAME = %s
                """,
                (f"tab{doctype}", index_name),
            )
            self.assertTrue(rows, f"missing Ads index {index_name}")

    def test_money_and_lifecycle_fields_use_hardened_schema(self):
        meta = frappe.get_meta("AOS Ad")
        self.assertEqual(meta.get_field("price").fieldtype, "Currency")
        self.assertEqual(meta.get_field("offer_price").fieldtype, "Currency")
        self.assertEqual(meta.get_field("price_type").fieldtype, "Select")
        self.assertTrue(meta.get_field("status").read_only)
        for field in (
            "status_changed_on",
            "published_on",
            "sold_on",
            "renewed_on",
            "expired_on",
            "deleted_on",
        ):
            self.assertIsNotNone(meta.get_field(field))

    def test_attribute_values_retain_historical_schema_snapshot(self):
        meta = frappe.get_meta("AOS Ad Attribute Value")
        for field in ("attribute_key", "attribute_label", "attribute_type", "attribute_unit"):
            self.assertIsNotNone(meta.get_field(field))
