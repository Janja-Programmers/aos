from __future__ import annotations

import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.catalog.categories import get_categories_impl
from aos.api.catalog.schema import get_category_schema_impl
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

    def tearDown(self):
        frappe.set_user("Administrator")
        for name in reversed(self.created_categories):
            if frappe.db.exists("AOS Category", name):
                frappe.delete_doc("AOS Category", name, ignore_permissions=True, force=True)
        for name in reversed(self.created_attributes):
            if frappe.db.exists("AOS Ad Attribute", name):
                frappe.delete_doc("AOS Ad Attribute", name, ignore_permissions=True, force=True)
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

    def _leaf(self, parent, suffix="Leaf", *, active=1, attributes=None):
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
            }
        ).insert(ignore_permissions=True)
        self.created_categories.append(doc.name)
        return doc

    def _attribute(self, suffix="Condition", field_type="Select"):
        label = f"{self.prefix} {suffix}"
        doc = frappe.get_doc(
            {
                "doctype": "AOS Ad Attribute",
                "label": label,
                "field_type": field_type,
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

        schema = CatalogService().get_public_schema(leaf.name)
        self.assertEqual(schema["category"]["id"], leaf.name)
        self.assertEqual(schema["attributes"][0]["id"], attribute.name)
        self.assertEqual(schema["attributes"][0]["options"], ["New", "Used"])

    def test_inactive_ancestor_removes_leaf_from_public_discovery(self):
        group = self._group()
        leaf = self._leaf(group.name)
        frappe.db.set_value("AOS Category", group.name, "is_active", 0, update_modified=False)
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
