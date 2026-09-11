from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from aos.api.catalog.categories import get_categories_impl
from aos.api.catalog.options import get_attribute_options_impl
from aos.api.catalog.schema import get_category_schema_impl
from aos.services.catalog.errors import CatalogDataError, CatalogNotFoundError


class TestCatalogAPI(TestCase):
    @patch("aos.api.catalog.categories.rate_limit", return_value=None)
    @patch("aos.api.catalog.categories.CatalogService")
    def test_categories_returns_standard_envelope(self, service_factory, _rate_limit):
        service_factory.return_value.list_public_categories.return_value = [{"id": "Root"}]
        response = get_categories_impl()
        self.assertTrue(response["ok"])
        self.assertEqual(response["data"], [{"id": "Root"}])

    @patch("aos.api.catalog.categories.rate_limit", return_value=None)
    @patch("aos.api.catalog.categories.CatalogService")
    def test_categories_rejects_unknown_fields_before_service(self, service_factory, _rate_limit):
        response = get_categories_impl(offset=0)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "INVALID_CATALOG_INPUT")
        service_factory.assert_not_called()

    @patch("aos.api.catalog.schema.rate_limit", return_value=None)
    @patch("aos.api.catalog.schema.CatalogService")
    def test_schema_requires_exact_category_field(self, service_factory, _rate_limit):
        missing = get_category_schema_impl()
        unknown = get_category_schema_impl(category="Phones", category_id="Phones")
        self.assertEqual(missing["error"], "INVALID_CATALOG_INPUT")
        self.assertEqual(unknown["error"], "INVALID_CATALOG_INPUT")
        service_factory.assert_not_called()

    @patch("aos.api.catalog.schema.rate_limit", return_value=None)
    @patch("aos.api.catalog.schema.CatalogService")
    def test_schema_returns_canonical_projection(self, service_factory, _rate_limit):
        service_factory.return_value.get_public_schema.return_value = {
            "category": {"id": "Phones", "is_group": 0, "image_url": None},
            "attributes": [],
            "pricing": {"requirement": "Optional", "allowed_price_types": [], "allowed_units": []},
        }
        response = get_category_schema_impl(category="Phones")
        self.assertTrue(response["ok"])
        self.assertEqual(response["data"]["pricing"]["requirement"], "Optional")

    @patch("aos.api.catalog.schema.rate_limit", return_value=None)
    @patch("aos.api.catalog.schema.CatalogService")
    def test_not_found_is_public_safe(self, service_factory, _rate_limit):
        service_factory.return_value.get_public_schema.side_effect = CatalogNotFoundError("private detail")
        response = get_category_schema_impl(category="Missing")
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "CATEGORY_NOT_FOUND")
        self.assertEqual(response["message"], "Category not found.")

    @patch("aos.api.catalog.categories.frappe.log_error")
    @patch("aos.api.catalog.categories.frappe.get_traceback", return_value="trace")
    @patch("aos.api.catalog.categories.rate_limit", return_value=None)
    @patch("aos.api.catalog.categories.CatalogService")
    def test_corrupt_catalog_does_not_leak_internal_message(
        self, service_factory, _rate_limit, _traceback, log_error
    ):
        service_factory.return_value.list_public_categories.side_effect = CatalogDataError("SQL secret")
        response = get_categories_impl()
        self.assertEqual(response["error"], "CATALOG_DATA_ERROR")
        self.assertEqual(response["message"], "Failed to fetch categories.")
        log_error.assert_called_once()


    @patch("aos.api.catalog.options.rate_limit", return_value=None)
    @patch("aos.api.catalog.options.CatalogService")
    def test_attribute_options_are_strict_and_return_dependency_projection(self, service_factory, _rate_limit):
        service_factory.return_value.get_public_attribute_options.return_value = {
            "category_id": "Laptops",
            "attribute": {"id": "Model", "key": "model", "label": "Model"},
            "depends_on": {"attribute_id": "Brand", "attribute_key": "brand", "value": "HP"},
            "options": ["EliteBook", "ProBook"],
        }
        unknown = get_attribute_options_impl(
            category="Laptops", attribute="model", parent_value="HP", legacy=1
        )
        self.assertEqual(unknown["error"], "INVALID_CATALOG_INPUT")
        service_factory.assert_not_called()

        response = get_attribute_options_impl(
            category="Laptops", attribute="model", parent_value="HP"
        )
        self.assertTrue(response["ok"])
        self.assertEqual(response["data"]["options"], ["EliteBook", "ProBook"])
