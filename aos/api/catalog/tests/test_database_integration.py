from __future__ import annotations

import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.catalog.categories import get_categories_impl
from aos.api.catalog.options import get_attribute_options_impl
from aos.api.catalog.schema import get_category_schema_impl
from aos.patches.v1_0 import install_catalog_indexes
from aos.services.catalog.cache import clear_catalog_cache
from aos.services.catalog.errors import CatalogValidationError
from aos.services.catalog.service import CatalogService


class TestCatalogDatabaseIntegration(FrappeTestCase):
    def setUp(self):
        self.original_user = frappe.session.user
        frappe.set_user("Administrator")
        self.prefix = f"CAT-{uuid.uuid4().hex[:10]}"
        self.created_categories: list[str] = []
        self.created_attributes: list[str] = []
        frappe.local.response = {}
        clear_catalog_cache()

    def tearDown(self):
        frappe.set_user("Administrator")
        clear_catalog_cache()
        for name in reversed(self.created_categories):
            if frappe.db.exists("AOS Category", name):
                frappe.delete_doc("AOS Category", name, ignore_permissions=True, force=True)
        for name in reversed(self.created_attributes):
            if frappe.db.exists("AOS Ad Attribute", name):
                frappe.delete_doc("AOS Ad Attribute", name, ignore_permissions=True, force=True)
        clear_catalog_cache()
        frappe.set_user(self.original_user)

    def _group(self, suffix="Root", *, active=1):
        name = f"{self.prefix} {suffix}"
        doc = frappe.get_doc(
            {
                "doctype": "AOS Category",
                "category_name": name,
                "is_group": 1,
                "is_active": active,
                "sort_order": 1,
                "pricing_requirement": "Optional",
                "allowed_price_types": "Fixed\nNegotiable",
            }
        ).insert(ignore_permissions=True)
        self.created_categories.append(doc.name)
        return doc

    def _leaf(self, parent, suffix="Leaf", *, active=1, attributes=None, dependencies=None):
        name = f"{self.prefix} {suffix}"
        doc = frappe.get_doc(
            {
                "doctype": "AOS Category",
                "category_name": name,
                "parent_aos_category": parent,
                "is_group": 0,
                "is_active": active,
                "sort_order": 1,
                "pricing_requirement": "Required",
                "allowed_price_types": "Fixed\nNegotiable",
                "attributes": attributes or [],
                "attribute_dependencies": dependencies or [],
            }
        ).insert(ignore_permissions=True)
        self.created_categories.append(doc.name)
        return doc

    def _attribute(self, suffix="Condition", field_type="Select", options=""):
        label = f"{self.prefix} {suffix}"
        doc = frappe.get_doc(
            {
                "doctype": "AOS Ad Attribute",
                "label": label,
                "field_type": field_type,
                "options": options,
                "is_active": 1,
            }
        ).insert(ignore_permissions=True)
        self.created_attributes.append(doc.name)
        return doc

    def test_real_public_tree_and_schema_flow(self):
        group = self._group()
        attribute = self._attribute()
        leaf = self._leaf(
            group.name,
            attributes=[
                {
                    "attribute": attribute.name,
                    "sort_order": 1,
                    "is_required": 1,
                    "is_active": 1,
                    "options_override": "New\nUsed",
                }
            ],
        )

        tree = CatalogService().list_public_categories()
        root = next(item for item in tree if item["id"] == group.name)
        self.assertEqual(root["children"][0]["id"], leaf.name)
        self.assertNotIn("image_media", root)

        schema = CatalogService().get_public_schema(leaf.name)
        self.assertEqual(schema["category"]["id"], leaf.name)
        self.assertEqual(schema["attributes"][0]["id"], attribute.name)
        self.assertEqual(schema["attributes"][0]["key"], attribute.attribute_key)
        self.assertEqual(schema["attributes"][0]["options"], ["New", "Used"])
        self.assertEqual(schema["pricing"]["requirement"], "Required")

    def test_inactive_ancestor_removes_leaf_from_public_discovery(self):
        group = self._group()
        leaf = self._leaf(group.name)
        # Deliberately bypass hooks to prove ancestry is enforced at read time;
        # clear cache because direct db.set_value is not a supported admin path.
        frappe.db.set_value("AOS Category", group.name, "is_active", 0, update_modified=False)
        clear_catalog_cache()
        tree = CatalogService().list_public_categories()
        ids = {item["id"] for root in tree for item in [root, *root.get("children", [])]}
        self.assertNotIn(leaf.name, ids)

    def test_public_endpoints_use_safe_envelopes_against_real_database(self):
        group = self._group()
        leaf = self._leaf(group.name)
        with (
            patch("aos.api.catalog.categories.rate_limit", return_value=None),
            patch("aos.api.catalog.schema.rate_limit", return_value=None),
        ):
            categories = get_categories_impl()
            schema = get_category_schema_impl(category=leaf.name)
        self.assertTrue(categories["ok"])
        self.assertTrue(schema["ok"])
        self.assertNotIn("owner", schema["data"]["category"])
        self.assertNotIn("modified_by", schema["data"]["category"])
        self.assertNotIn("image_media", schema["data"]["category"])

    def test_group_cannot_be_used_as_sellable_ad_category(self):
        group = self._group()
        with self.assertRaises(CatalogValidationError):
            CatalogService().assert_sellable_category(group.name)

    def test_duplicate_category_attribute_is_rejected_before_insert(self):
        group = self._group()
        attribute = self._attribute(field_type="Text")
        with self.assertRaises(Exception):
            self._leaf(
                group.name,
                attributes=[
                    {"attribute": attribute.name, "is_active": 1},
                    {"attribute": attribute.name, "is_active": 1},
                ],
            )
        self.assertFalse(frappe.db.exists("AOS Category", f"{self.prefix} Leaf"))

    def test_stable_identity_metadata_is_enforced(self):
        group = self._group()
        attribute = self._attribute(field_type="Text")
        self.assertEqual(group.name, group.category_name)
        self.assertTrue(attribute.attribute_key)
        self.assertEqual(frappe.get_meta("AOS Category").allow_rename, 0)
        self.assertEqual(frappe.get_meta("AOS Ad Attribute").allow_rename, 0)

    def test_category_name_unique_constraint_is_enforced(self):
        group = self._group()
        duplicate = frappe.get_doc(
            {
                "doctype": "AOS Category",
                "category_name": group.category_name,
                "is_group": 1,
                "is_active": 1,
                "pricing_requirement": "Optional",
                "allowed_price_types": "Fixed",
            }
        )
        with self.assertRaises(Exception):
            duplicate.insert(ignore_permissions=True)

    def test_manual_catalog_indexes_exist_with_expected_order_and_uniqueness(self):
        install_catalog_indexes.execute()
        for doctype, index_name, columns, unique in install_catalog_indexes.INDEXES:
            rows = frappe.db.sql(
                """
                SELECT COLUMN_NAME, NON_UNIQUE
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
                ORDER BY SEQ_IN_INDEX
                """,
                (f"tab{doctype}", index_name),
                as_dict=True,
            )
            self.assertEqual(tuple(row.COLUMN_NAME for row in rows), columns)
            self.assertTrue(rows)
            self.assertEqual(not bool(int(rows[0].NON_UNIQUE)), unique)


    def test_real_dependent_attribute_flow(self):
        brand = self._attribute("Brand", options="HP\nApple")
        model = self._attribute("Model", options="")
        leaf = self._leaf(
            "",
            suffix="Laptops",
            attributes=[
                {
                    "attribute": brand.name,
                    "sort_order": 10,
                    "is_required": 1,
                    "is_active": 1,
                },
                {
                    "attribute": model.name,
                    "sort_order": 20,
                    "is_required": 1,
                    "is_active": 1,
                    "depends_on_attribute": brand.name,
                },
            ],
            dependencies=[
                {
                    "child_attribute": model.name,
                    "parent_option": "HP",
                    "child_options": "EliteBook\nProBook",
                },
                {
                    "child_attribute": model.name,
                    "parent_option": "Apple",
                    "child_options": "MacBook Air",
                },
            ],
        )

        refreshed = frappe.get_doc("AOS Category", leaf.name)
        self.assertEqual(len(refreshed.attribute_dependencies), 2)
        hp_mapping = next(row for row in refreshed.attribute_dependencies if row.parent_option == "HP")
        self.assertEqual(hp_mapping.child_options, "EliteBook\nProBook")
        self.assertTrue(all(len(row.mapping_key or "") == 64 for row in refreshed.attribute_dependencies))

        schema = CatalogService().get_public_schema(leaf.name)
        model_schema = next(item for item in schema["attributes"] if item["id"] == model.name)
        self.assertEqual(
            model_schema["depends_on"],
            {"id": brand.name, "key": brand.attribute_key},
        )
        self.assertEqual(
            model_schema["options"],
            ["EliteBook", "ProBook", "MacBook Air"],
        )
        self.assertNotIn("_dependency_options", model_schema)

        hp = CatalogService().get_public_attribute_options(
            category=leaf.name,
            attribute=model.attribute_key,
            parent_value="HP",
        )
        self.assertEqual(hp["options"], ["EliteBook", "ProBook"])
        apple = CatalogService().get_public_attribute_options(
            category=leaf.name,
            attribute=model.name,
            parent_value="Apple",
        )
        self.assertEqual(apple["options"], ["MacBook Air"])

        with patch("aos.api.catalog.options.rate_limit", return_value=None):
            response = get_attribute_options_impl(
                category=leaf.name,
                attribute=model.attribute_key,
                parent_value="HP",
            )
        self.assertTrue(response["ok"])
        self.assertEqual(response["data"]["options"], ["EliteBook", "ProBook"])

    def test_dependent_attribute_requires_dependency_mappings(self):
        brand = self._attribute("Brand Incomplete", options="HP\nApple")
        model = self._attribute("Model Incomplete", options="")
        with self.assertRaises(Exception):
            self._leaf(
                "",
                suffix="Broken Laptops",
                attributes=[
                    {"attribute": brand.name, "is_required": 1, "is_active": 1},
                    {
                        "attribute": model.name,
                        "is_required": 1,
                        "is_active": 1,
                        "depends_on_attribute": brand.name,
                    },
                ],
            )
