from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.patches.v1_0 import harden_shorts_subsystem


class TestShortsDatabaseContracts(FrappeTestCase):
    def test_shorts_patch_is_idempotent_and_indexes_exist(self):
        harden_shorts_subsystem.execute()
        harden_shorts_subsystem.execute()
        expected = {
            "uq_short_metrics_day": "AOS Short Metrics Daily",
            "uq_short_sound_link": "AOS Short Sound",
            "uq_short_report_active": "AOS Short Report",
            "uq_short_repost_active": "AOS Short Repost",
            "uq_short_processing_active": "AOS Video Processing Job",
            "uq_short_event_key": "AOS Short Event",
            "idx_short_feed": "AOS Short",
            "idx_short_profile": "AOS Short",
            "idx_short_comment_page": "AOS Short Comment",
            "idx_short_report_review": "AOS Short Report",
            "idx_short_job_lifecycle": "AOS Video Processing Job",
        }
        for name, doctype in expected.items():
            rows = frappe.db.sql(
                """SELECT INDEX_NAME FROM information_schema.STATISTICS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1""",
                (f"tab{doctype}", name),
            )
            self.assertTrue(rows, f"missing Shorts index {name}")

    def test_active_uniqueness_fields_are_fixed_size_and_not_public(self):
        for doctype, fieldname in (
            ("AOS Short Report", "active_key"),
            ("AOS Short Repost", "active_key"),
            ("AOS Video Processing Job", "active_key"),
            ("AOS Short Event", "event_key"),
        ):
            field = frappe.get_meta(doctype).get_field(fieldname)
            self.assertIsNotNone(field)
            self.assertTrue(field.hidden)
            self.assertTrue(field.read_only)
            self.assertLessEqual(int(field.length or 140), 64)

    def test_processing_generation_field_exists(self):
        field = frappe.get_meta("AOS Video Processing Job").get_field("generation")
        self.assertIsNotNone(field)
        self.assertEqual(field.fieldtype, "Int")
