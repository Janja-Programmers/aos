from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from aos.services.catalog.errors import CatalogValidationError
from aos.services.catalog.validation import (
    normalize_category_id,
    normalize_flag,
    normalize_sort_order,
    normalize_text,
    split_choices,
    validate_attribute_document,
    validate_category_document,
)


class TestCatalogValidation(TestCase):
    def test_text_normalizes_unicode_and_whitespace(self):
        self.assertEqual(
            normalize_text("  Cafe\u0301  \n Gear ", field="name", max_length=40, required=True),
            "Café Gear",
        )

    def test_text_rejects_structured_input(self):
        with self.assertRaises(CatalogValidationError):
            normalize_category_id({"name": "Beauty"})

    def test_text_rejects_null_bytes(self):
        with self.assertRaises(CatalogValidationError):
            normalize_category_id("Beauty\x00")

    def test_sort_order_is_bounded_and_integral(self):
        self.assertEqual(normalize_sort_order("8"), 8)
        for value in (-1, 1.5, True, {"value": 1}):
            with self.subTest(value=value), self.assertRaises(CatalogValidationError):
                normalize_sort_order(value)

    def test_flags_accept_only_boolean_storage_values(self):
        self.assertEqual(normalize_flag("0", field="active"), 0)
        self.assertEqual(normalize_flag("1", field="active"), 1)
        with self.assertRaises(CatalogValidationError):
            normalize_flag("yes", field="active")

    def test_choices_are_trimmed_and_deduplicated(self):
        self.assertEqual(split_choices(" Fixed \nFixed\nFree", field="price"), ["Fixed", "Free"])

    def test_choices_reject_invalid_allowlist_value(self):
        with self.assertRaises(CatalogValidationError):
            split_choices("Auction", field="price", allowed={"Fixed"})

    def test_choices_reject_structured_input(self):
        with self.assertRaises(CatalogValidationError):
            split_choices(["Fixed"], field="price")

    @patch("aos.services.catalog.validation.frappe.get_all", return_value=[])
    @patch("aos.services.catalog.validation.frappe.db.get_value")
    def test_group_cannot_have_parent(self, get_value, _get_all):
        get_value.return_value = {"name": "Root", "is_group": 1, "parent_aos_category": None}
        doc = SimpleNamespace(
            name="Nested",
            category_name="Nested",
            parent_aos_category="Root",
            is_group=1,
            is_active=1,
            is_service=0,
            sort_order=1,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[],
        )
        with self.assertRaises(CatalogValidationError) as exc:
            validate_category_document(doc)
        self.assertEqual(exc.exception.code, "INVALID_CATEGORY_TREE")

    @patch("aos.services.catalog.validation.frappe.get_all", return_value=[])
    @patch("aos.services.catalog.validation.frappe.db.get_value")
    def test_leaf_requires_root_group_parent(self, get_value, _get_all):
        get_value.return_value = {"name": "Parent", "is_group": 0, "parent_aos_category": None}
        doc = SimpleNamespace(
            name="Leaf",
            category_name="Leaf",
            parent_aos_category="Parent",
            is_group=0,
            is_active=1,
            is_service=0,
            sort_order=1,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[],
        )
        with self.assertRaises(CatalogValidationError):
            validate_category_document(doc)

    @patch("aos.services.catalog.validation.frappe.get_all", return_value=[])
    @patch("aos.services.catalog.validation.frappe.db.get_value")
    def test_non_service_rejects_price_units(self, get_value, _get_all):
        get_value.return_value = {"name": "Root", "is_group": 1, "parent_aos_category": None}
        doc = SimpleNamespace(
            name="Leaf",
            category_name="Leaf",
            parent_aos_category="Root",
            is_group=0,
            is_active=1,
            is_service=0,
            sort_order=1,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="hour",
            attributes=[],
        )
        with self.assertRaises(CatalogValidationError):
            validate_category_document(doc)

    def test_attribute_rejects_options_for_non_select_type(self):
        doc = SimpleNamespace(
            label="Weight",
            field_type="Number",
            unit="kg",
            help_text="Weight",
            options="Small\nLarge",
            is_active=1,
        )
        with self.assertRaises(CatalogValidationError):
            validate_attribute_document(doc)

    def test_select_attribute_can_use_category_level_options(self):
        doc = SimpleNamespace(
            label="Brand",
            field_type="Select",
            unit="",
            help_text="",
            options="",
            is_active=1,
        )
        validate_attribute_document(doc)
        self.assertEqual(doc.options, "")
