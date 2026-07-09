from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.shared.responses import fail, ok


class TestSharedResponses(FrappeTestCase):
    def setUp(self):
        frappe.local.response = {}

    def test_ok_preserves_falsy_data(self):
        cases = [[], False, 0, ""]
        for value in cases:
            with self.subTest(value=repr(value)):
                response = ok("Fetched.", data=value)
                self.assertTrue(response["ok"])
                self.assertIs(response["data"], value)

    def test_ok_defaults_data_only_when_none(self):
        self.assertEqual(ok("Fetched.")["data"], {})
        self.assertEqual(ok("Fetched.", data=None)["data"], {})

    def test_fail_preserves_falsy_data(self):
        cases = [[], False, 0, ""]
        for value in cases:
            with self.subTest(value=repr(value)):
                response = fail("Bad.", error="VALIDATION_ERROR", data=value)
                self.assertFalse(response["ok"])
                self.assertIs(response["data"], value)

    def test_fail_defaults_data_only_when_none(self):
        self.assertEqual(fail("Bad.", error="VALIDATION_ERROR")["data"], {})
        self.assertEqual(fail("Bad.", error="VALIDATION_ERROR", data=None)["data"], {})
        self.assertNotIn("code", fail("Bad.", error="VALIDATION_ERROR"))
