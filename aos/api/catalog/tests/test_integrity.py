from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from aos.services.catalog.errors import CatalogConflictError, CatalogValidationError
from aos.services.catalog.integrity import (
    assert_attribute_identity_immutable,
    assert_attribute_schema_change_safe,
    assert_category_schema_change_safe,
    lock_category_mutation,
    lock_category_schema_for_ad,
)


class FakeDoc(SimpleNamespace):
    def is_new(self):
        return bool(getattr(self, "_new", False))

    def get_doc_before_save(self):
        return getattr(self, "_before", None)


def relation(attribute, *, required=0, active=1, options="", order=0, depends_on=""):
    return SimpleNamespace(
        attribute=attribute,
        is_required=required,
        is_active=active,
        options_override=options,
        sort_order=order,
        depends_on_attribute=depends_on,
    )


class TestCatalogIntegrity(TestCase):
    @patch("aos.services.catalog.integrity.frappe.db.sql")
    def test_existing_category_lock_protocol_is_target_then_parents(self, sql):
        before = FakeDoc(parent_aos_category="Old Root", attributes=[relation("Old Attribute")])
        doc = FakeDoc(
            _new=False,
            name="Leaf",
            parent_aos_category="New Root",
            attributes=[relation("New Attribute")],
            _before=before,
        )
        lock_category_mutation(doc)
        self.assertEqual(sql.call_count, 3)
        self.assertIn("WHERE name=%s FOR UPDATE", sql.call_args_list[0].args[0])
        self.assertEqual(sql.call_args_list[0].args[1], ("Leaf",))
        self.assertIn("ORDER BY name FOR UPDATE", sql.call_args_list[1].args[0])
        self.assertEqual(sql.call_args_list[1].args[1], ("New Root", "Old Root"))
        self.assertIn("tabAOS Ad Attribute", sql.call_args_list[2].args[0])
        self.assertEqual(sql.call_args_list[2].args[1], ("New Attribute", "Old Attribute"))

    @patch("aos.services.catalog.integrity.frappe.db.sql")
    def test_ads_lock_protocol_locks_leaf_then_parent(self, sql):
        sql.side_effect = [
            [{"name": "Leaf", "parent_aos_category": "Root"}],
            [],
            [{"name": "ROW-1", "attribute": "Condition"}],
            [],
            [],
        ]
        lock_category_schema_for_ad("Leaf")
        self.assertEqual(sql.call_count, 5)
        self.assertEqual(sql.call_args_list[0].args[1], ("Leaf",))
        self.assertEqual(sql.call_args_list[1].args[1], ("Root",))
        self.assertEqual(sql.call_args_list[2].args[1], ("Leaf", "Root"))
        self.assertIn("tabAOS Category Attribute Row", sql.call_args_list[2].args[0])
        self.assertIn("tabAOS Category Attribute Dependency Row", sql.call_args_list[3].args[0])
        self.assertEqual(sql.call_args_list[3].args[1], ("Leaf", "Root"))
        self.assertIn("tabAOS Ad Attribute", sql.call_args_list[4].args[0])
        self.assertEqual(sql.call_args_list[4].args[1], ("Condition",))
        self.assertTrue(all("FOR UPDATE" in call.args[0] for call in sql.call_args_list))

    @patch("aos.services.catalog.integrity.frappe.db.exists")
    def test_dependency_parent_global_option_expansion_is_blocked(self, exists):
        exists.side_effect = lambda doctype, filters: (
            doctype == "AOS Category Attribute Row" and "depends_on_attribute" in filters
        )
        before = FakeDoc(field_type="Select", options="HP\nApple", is_active=1)
        expanded = FakeDoc(
            _new=False,
            name="Brand",
            field_type="Select",
            options="HP\nApple\nDell",
            is_active=1,
            _before=before,
        )
        with self.assertRaises(CatalogValidationError):
            assert_attribute_schema_change_safe(expanded)


    @patch("aos.services.catalog.integrity.frappe.get_all", return_value=[])
    @patch("aos.services.catalog.integrity.frappe.db.exists", return_value=True)
    def test_used_leaf_cannot_move_or_change_pricing(self, _exists, _get_all):
        before = FakeDoc(
            parent_aos_category="Root",
            is_group=0,
            is_service=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[],
        )
        moved = FakeDoc(
            _new=False,
            name="Leaf",
            parent_aos_category="Other Root",
            is_group=0,
            is_service=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[],
            _before=before,
        )
        with self.assertRaises(CatalogValidationError) as exc:
            assert_category_schema_change_safe(moved)
        self.assertEqual(exc.exception.code, "CATEGORY_IN_USE")

    @patch("aos.services.catalog.integrity.frappe.get_all", return_value=[])
    @patch("aos.services.catalog.integrity.frappe.db.exists", return_value=True)
    def test_used_category_allows_optional_attribute_add_but_not_required_add(self, _exists, _get_all):
        before = FakeDoc(
            parent_aos_category="Root",
            is_group=0,
            is_service=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[],
        )
        optional = FakeDoc(
            _new=False,
            name="Leaf",
            parent_aos_category="Root",
            is_group=0,
            is_service=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[relation("Color", required=0)],
            _before=before,
        )
        assert_category_schema_change_safe(optional)
        optional.attributes = [relation("Color", required=1)]
        with self.assertRaises(CatalogValidationError):
            assert_category_schema_change_safe(optional)

    @patch("aos.services.catalog.integrity.frappe.get_all", return_value=[])
    @patch("aos.services.catalog.integrity.frappe.db.exists", return_value=True)
    def test_used_category_select_override_can_expand_but_not_narrow(self, _exists, _get_all):
        before = FakeDoc(
            parent_aos_category="Root",
            is_group=0,
            is_service=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[relation("Color", options="Red\nBlue")],
        )
        doc = FakeDoc(
            _new=False,
            name="Leaf",
            parent_aos_category="Root",
            is_group=0,
            is_service=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[relation("Color", options="Red\nBlue\nGreen")],
            _before=before,
        )
        assert_category_schema_change_safe(doc)
        doc.attributes = [relation("Color", options="Red")]
        with self.assertRaises(CatalogValidationError):
            assert_category_schema_change_safe(doc)

    def test_attribute_key_is_immutable(self):
        before = FakeDoc(attribute_key="fuel_type")
        doc = FakeDoc(_new=False, attribute_key="other", _before=before)
        with self.assertRaises(CatalogConflictError):
            assert_attribute_identity_immutable(doc)

    @patch("aos.services.catalog.integrity.frappe.db.exists")
    def test_used_attribute_type_and_option_removal_are_blocked(self, exists):
        exists.side_effect = lambda doctype, _filters: doctype == "AOS Ad Attribute Value"
        before = FakeDoc(field_type="Select", options="Red\nBlue")
        changed_type = FakeDoc(_new=False, name="Color", field_type="Text", options="", _before=before)
        with self.assertRaises(CatalogValidationError):
            assert_attribute_schema_change_safe(changed_type)

        narrowed = FakeDoc(_new=False, name="Color", field_type="Select", options="Red", _before=before)
        with self.assertRaises(CatalogValidationError):
            assert_attribute_schema_change_safe(narrowed)

        expanded = FakeDoc(
            _new=False,
            name="Color",
            field_type="Select",
            options="Red\nBlue\nGreen",
            _before=before,
        )
        assert_attribute_schema_change_safe(expanded)


    @patch("aos.services.catalog.integrity.frappe.db.get_value", return_value="HP\nApple")
    @patch("aos.services.catalog.integrity.frappe.get_all", return_value=[])
    @patch("aos.services.catalog.integrity.frappe.db.exists", return_value=True)
    def test_used_category_dependency_mappings_may_expand_additively(
        self, _exists, _get_all, _get_value
    ):
        before = FakeDoc(
            parent_aos_category="",
            is_group=0,
            is_service=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[
                relation("Brand", required=1, options="HP\nApple"),
                relation("Model", required=1, options="EliteBook", depends_on="Brand"),
            ],
            attribute_dependencies=[
                SimpleNamespace(child_attribute="Model", parent_option="HP", child_options="EliteBook")
            ],
        )
        doc = FakeDoc(
            _new=False,
            name="Laptops",
            parent_aos_category="",
            is_group=0,
            is_service=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[
                relation("Brand", required=1, options="HP\nApple"),
                relation(
                    "Model",
                    required=1,
                    options="EliteBook\nMacBook Air",
                    depends_on="Brand",
                ),
            ],
            attribute_dependencies=[
                SimpleNamespace(child_attribute="Model", parent_option="HP", child_options="EliteBook"),
                SimpleNamespace(child_attribute="Model", parent_option="Apple", child_options="MacBook Air"),
            ],
            _before=before,
        )
        assert_category_schema_change_safe(doc)

    @patch("aos.services.catalog.integrity.frappe.db.get_value", return_value="HP\nApple")
    @patch("aos.services.catalog.integrity.frappe.get_all", return_value=[])
    @patch("aos.services.catalog.integrity.frappe.db.exists", return_value=True)
    def test_used_category_can_promote_global_options_to_additive_override(
        self, _exists, _get_all, _get_value
    ):
        before = FakeDoc(
            parent_aos_category="",
            is_group=0,
            is_service=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[relation("Brand", options="")],
        )
        doc = FakeDoc(
            _new=False,
            name="Laptops",
            parent_aos_category="",
            is_group=0,
            is_service=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[relation("Brand", options="HP\nApple\nDell")],
            _before=before,
        )
        assert_category_schema_change_safe(doc)


    @patch("aos.services.catalog.integrity.frappe.get_all", return_value=[])
    @patch("aos.services.catalog.integrity.frappe.db.exists", return_value=True)
    def test_used_category_dependency_rules_cannot_change_in_place(self, _exists, _get_all):
        before = FakeDoc(
            parent_aos_category="",
            is_group=0,
            is_service=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[relation("Brand", required=1), relation("Model", required=1, depends_on="Brand")],
            attribute_dependencies=[
                SimpleNamespace(child_attribute="Model", parent_option="HP", child_options="EliteBook")
            ],
        )
        doc = FakeDoc(
            _new=False,
            name="Laptops",
            parent_aos_category="",
            is_group=0,
            is_service=0,
            pricing_requirement="Optional",
            allowed_price_types="Fixed",
            allowed_price_units="",
            attributes=[relation("Brand", required=1), relation("Model", required=1, depends_on="Brand")],
            attribute_dependencies=[
                SimpleNamespace(child_attribute="Model", parent_option="Apple", child_options="EliteBook")
            ],
            _before=before,
        )
        with self.assertRaises(CatalogValidationError):
            assert_category_schema_change_safe(doc)
