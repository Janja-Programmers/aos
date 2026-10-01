from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from aos.services.catalog.constants import MAX_ATTRIBUTE_OPTIONS
from aos.services.catalog.errors import CatalogValidationError
from aos.services.catalog.validation import (
    canonical_attribute_key,
    reject_unknown_fields,
    validate_attribute_document,
    validate_category_document,
)


class FakeAttribute(SimpleNamespace):
    def is_new(self):
        return bool(getattr(self, "_new", False))


class TestCatalogValidation(TestCase):
    def test_canonical_attribute_key_is_deterministic(self):
        self.assertEqual(canonical_attribute_key("Fuel Type"), "fuel_type")
        self.assertEqual(canonical_attribute_key("  Fuel   Type  "), "fuel_type")
        self.assertTrue(canonical_attribute_key("燃料").startswith("attribute_"))

    def test_new_attribute_key_is_generated_and_client_value_is_ignored(self):
        doc = FakeAttribute(
            _new=True,
            label="Fuel Type",
            attribute_key="client_override",
            field_type="Text",
            unit="",
            help_text="",
            options="",
            is_active=1,
        )
        validate_attribute_document(doc)
        self.assertEqual(doc.attribute_key, "fuel_type")

    def test_existing_attribute_requires_persisted_key(self):
        doc = FakeAttribute(
            _new=False,
            label="Fuel Type",
            attribute_key="",
            field_type="Text",
            unit="",
            help_text="",
            options="",
            is_active=1,
        )
        with self.assertRaises(CatalogValidationError):
            validate_attribute_document(doc)

    def test_non_select_attribute_rejects_options(self):
        doc = FakeAttribute(
            _new=True,
            label="Weight",
            attribute_key="",
            field_type="Number",
            unit="kg",
            help_text="",
            options="1\n2",
            is_active=1,
        )
        with self.assertRaises(CatalogValidationError):
            validate_attribute_document(doc)

    def test_request_fields_are_strict(self):
        reject_unknown_fields({"category": "Phones"}, allowed={"category"})
        with self.assertRaises(CatalogValidationError) as exc:
            reject_unknown_fields({"category": "Phones", "unexpected": 1}, allowed={"category"})
        self.assertEqual(exc.exception.code, "INVALID_CATALOG_INPUT")

    @patch("aos.services.catalog.validation.frappe.db.get_value")
    @patch("aos.services.catalog.validation.frappe.get_all")
    def test_category_parent_must_be_root_group_and_options_require_select(self, get_all, get_value):
        get_value.return_value = {"name": "Root", "is_group": 1, "parent_aos_category": None}
        get_all.return_value = [
            {"name": "Type of Service", "field_type": "Text", "options": "", "is_active": 1}
        ]
        row = SimpleNamespace(
            attribute="Type of Service",
            sort_order=1,
            is_active=1,
            is_required=1,
            options_override="Visa Service\nTours",
        )
        doc = SimpleNamespace(
            name="Travel",
            category_name="Travel",
            parent_aos_category="Root",
            is_group=0,
            is_active=1,
            is_service=1,
            sort_order=1,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="Per job",
            attributes=[row],
        )
        with self.assertRaises(CatalogValidationError):
            validate_category_document(doc)

    @patch("aos.services.catalog.validation.frappe.db.get_value")
    @patch("aos.services.catalog.validation.frappe.get_all")
    def test_duplicate_category_attribute_is_rejected(self, get_all, get_value):
        get_value.return_value = None
        rows = [
            SimpleNamespace(attribute="Brand", sort_order=1, is_active=1, is_required=0, options_override=""),
            SimpleNamespace(attribute="Brand", sort_order=2, is_active=1, is_required=0, options_override=""),
        ]
        doc = SimpleNamespace(
            name="Phones",
            category_name="Phones",
            parent_aos_category="",
            is_group=0,
            is_active=1,
            is_service=0,
            sort_order=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=rows,
        )
        with self.assertRaises(CatalogValidationError):
            validate_category_document(doc)
        get_all.assert_not_called()

    @patch("aos.services.catalog.validation.frappe.get_all")
    def test_select_override_is_large_but_bounded(self, get_all):
        get_all.return_value = [
            {"name": "Brand", "field_type": "Select", "options": "", "is_active": 1}
        ]
        options = "\n".join(f"Brand {index}" for index in range(MAX_ATTRIBUTE_OPTIONS))
        row = SimpleNamespace(attribute="Brand", sort_order=1, is_active=1, is_required=0, options_override=options)
        doc = SimpleNamespace(
            name="Phones",
            category_name="Phones",
            parent_aos_category="",
            is_group=0,
            is_active=1,
            is_service=0,
            sort_order=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[row],
        )
        validate_category_document(doc)
        self.assertEqual(len(row.options_override.splitlines()), MAX_ATTRIBUTE_OPTIONS)
        row.options_override += "\nToo many"
        with self.assertRaises(CatalogValidationError):
            validate_category_document(doc)


    @patch("aos.services.catalog.validation.frappe.get_all")
    def test_dependent_select_attributes_require_complete_acyclic_mappings(self, get_all):
        get_all.return_value = [
            {"name": "Brand", "field_type": "Select", "options": "HP\nApple", "is_active": 1},
            {"name": "Model", "field_type": "Select", "options": "Previous Model", "is_active": 1},
        ]
        brand = SimpleNamespace(
            attribute="Brand",
            sort_order=1,
            is_active=1,
            is_required=1,
            options_override="",
            depends_on_attribute="",
        )
        model = SimpleNamespace(
            attribute="Model",
            sort_order=2,
            is_active=1,
            is_required=1,
            options_override="",
            depends_on_attribute="Brand",
        )
        mappings = [
            SimpleNamespace(child_attribute="Model", parent_option="HP", child_options="EliteBook"),
            SimpleNamespace(child_attribute="Model", parent_option="Apple", child_options="MacBook Air"),
        ]
        doc = SimpleNamespace(
            name="Laptops",
            category_name="Laptops",
            parent_aos_category="",
            is_group=0,
            is_active=1,
            is_service=0,
            sort_order=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[brand, model],
            attribute_dependencies=mappings,
        )
        validate_category_document(doc)
        self.assertEqual(mappings[0].child_options, "EliteBook")
        self.assertEqual(len(mappings[0].mapping_key), 64)

        model.options_override = "EliteBook\nMacBook Air"
        with self.assertRaises(CatalogValidationError):
            validate_category_document(doc)
        model.options_override = ""

        doc.attribute_dependencies = mappings[:1]
        with self.assertRaises(CatalogValidationError):
            validate_category_document(doc)

        doc.attribute_dependencies = [
            SimpleNamespace(
                child_attribute="Model",
                parent_option="HP",
                child_options="EliteBook\nMacBook Air",
            ),
        ]
        with self.assertRaises(CatalogValidationError):
            validate_category_document(doc)

        doc.attribute_dependencies = [
            SimpleNamespace(child_attribute="Model", parent_option="HP", child_options="EliteBook"),
            SimpleNamespace(child_attribute="Model", parent_option="HP", child_options="MacBook Air"),
            SimpleNamespace(child_attribute="Model", parent_option="Apple", child_options="MacBook Air"),
        ]
        with self.assertRaises(CatalogValidationError):
            validate_category_document(doc)

        doc.attribute_dependencies = mappings
        brand.depends_on_attribute = "Model"
        with self.assertRaises(CatalogValidationError):
            validate_category_document(doc)
    @patch("aos.services.catalog.validation.frappe.db.get_value")
    @patch("aos.services.catalog.validation.frappe.get_all")
    def test_leaf_parent_override_cannot_break_inherited_dependency(self, get_all, get_value):
        get_value.return_value = {"name": "Computers", "is_group": 1, "parent_aos_category": None}

        def rows(doctype, **kwargs):
            if doctype == "AOS Ad Attribute":
                return [
                    {"name": "Brand", "field_type": "Select", "options": "HP\nApple", "is_active": 1}
                ]
            if doctype == "AOS Category Attribute Row":
                return [{"attribute": "Model", "depends_on_attribute": "Brand", "is_required": 1}]
            if doctype == "AOS Category Attribute Dependency Row":
                return [{"parent_option": "HP"}, {"parent_option": "Apple"}]
            return []

        get_all.side_effect = rows
        brand = SimpleNamespace(
            attribute="Brand",
            sort_order=1,
            is_active=1,
            is_required=1,
            options_override="HP",
            depends_on_attribute="",
        )
        doc = SimpleNamespace(
            name="Laptops",
            category_name="Laptops",
            parent_aos_category="Computers",
            is_group=0,
            is_active=1,
            is_service=0,
            sort_order=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[brand],
            attribute_dependencies=[],
        )
        with self.assertRaises(CatalogValidationError) as exc:
            validate_category_document(doc)
        self.assertEqual(exc.exception.code, "INVALID_CATEGORY_SCHEMA")

        brand.options_override = "HP\nApple\nDell"
        brand.is_required = 0
        with self.assertRaises(CatalogValidationError):
            validate_category_document(doc)

        brand.is_required = 1
        brand.options_override = "HP\nApple"
        validate_category_document(doc)
