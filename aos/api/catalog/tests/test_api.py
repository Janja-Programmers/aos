from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.catalog.categories import get_categories_impl
from aos.api.catalog.schema import get_category_schema_impl
from aos.services.catalog.errors import CatalogDataError, CatalogNotFoundError


class TestCatalogAPI(FrappeTestCase):
    def setUp(self):
        frappe.local.response = {}

    @patch("aos.api.catalog.categories.rate_limit", return_value=None)
    @patch("aos.api.catalog.categories.CatalogService")
    def test_categories_returns_stable_envelope(self, service_factory, _rate_limit):
        service_factory.return_value.list_public_categories.return_value = [{"id": "Root", "children": []}]
        response = get_categories_impl()
        self.assertTrue(response["ok"])
        self.assertEqual(response["data"][0]["id"], "Root")
        self.assertEqual(frappe.local.response["http_status_code"], 200)

    @patch("aos.api.catalog.categories.frappe.log_error")
    @patch("aos.api.catalog.categories.rate_limit", return_value=None)
    @patch("aos.api.catalog.categories.CatalogService")
    def test_categories_hides_internal_error_details(
        self, service_factory, _rate_limit, _log_error
    ):
        service_factory.return_value.list_public_categories.side_effect = CatalogDataError("cycle at private-id")
        response = get_categories_impl()
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "CATALOG_DATA_ERROR")
        self.assertNotIn("private-id", response["message"])
        self.assertEqual(frappe.local.response["http_status_code"], 500)

    @patch("aos.api.catalog.schema.rate_limit", return_value=None)
    @patch("aos.api.catalog.schema.CatalogService")
    def test_schema_returns_safe_shape(self, service_factory, _rate_limit):
        service_factory.return_value.get_public_schema.return_value = {
            "category": {"id": "Leaf", "name": "Leaf", "parent_id": "Root", "is_group": 0, "is_service": 0},
            "attributes": [],
            "pricing": {"pricing_requirement": "Optional"},
        }
        response = get_category_schema_impl(category="Leaf")
        self.assertTrue(response["ok"])
        self.assertNotIn("owner", response["data"]["category"])

    @patch("aos.api.catalog.schema.rate_limit", return_value=None)
    @patch("aos.api.catalog.schema.CatalogService")
    def test_schema_not_found_is_generic(self, service_factory, _rate_limit):
        service_factory.return_value.get_public_schema.side_effect = CatalogNotFoundError("Category not found.")
        response = get_category_schema_impl(category="Missing")
        self.assertEqual(response["error"], "CATEGORY_NOT_FOUND")
        self.assertEqual(response["message"], "Category not found.")
        self.assertEqual(frappe.local.response["http_status_code"], 404)

    @patch("aos.api.catalog.schema.rate_limit", return_value=None)
    def test_schema_requires_category(self, _rate_limit):
        response = get_category_schema_impl()
        self.assertEqual(response["error"], "VALIDATION_ERROR")
        self.assertEqual(frappe.local.response["http_status_code"], 422)

    @patch("aos.api.catalog.schema.rate_limit", return_value=None)
    def test_schema_rejects_structured_category_input(self, _rate_limit):
        response = get_category_schema_impl(category={"name": "Leaf"})
        self.assertEqual(response["error"], "INVALID_CATALOG_INPUT")
        self.assertEqual(frappe.local.response["http_status_code"], 422)

    @patch("aos.api.catalog.schema.rate_limit")
    def test_schema_rate_limit_short_circuits_before_database_work(self, rate_limit):
        rate_limit.return_value = {"ok": False, "error": "RATE_LIMIT", "message": "Limited", "data": {}}
        with patch("aos.api.catalog.schema.CatalogService") as service_factory:
            response = get_category_schema_impl(category="Leaf")
        self.assertEqual(response["error"], "RATE_LIMIT")
        service_factory.assert_not_called()
