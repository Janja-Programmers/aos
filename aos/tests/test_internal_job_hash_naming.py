from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase


class TestInternalJobHashNaming(FrappeTestCase):
    def test_high_write_internal_jobs_do_not_use_shared_naming_series(self):
        for doctype in (
            "AOS Search Index Job",
            "AOS Moderation Job",
            "AOS Video Processing Job",
            "AOS Notification Delivery Job",
            "AOS Analytics Ingest Job",
            "AOS Transactional Outbox",
        ):
            with self.subTest(doctype=doctype):
                self.assertEqual(frappe.get_meta(doctype).autoname, "hash")
