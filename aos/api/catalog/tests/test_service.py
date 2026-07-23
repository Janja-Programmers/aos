from __future__ import annotations

from unittest import TestCase

from aos.services.catalog.errors import CatalogDataError, CatalogNotFoundError, CatalogValidationError
from aos.services.catalog.service import CatalogService, resolve_attributes, resolve_pricing


class FakeCatalogRepository:
    def __init__(self, categories, rows=None, attributes=None):
        self.categories = categories
        self.rows = rows or []
        self.attributes = attributes or {}
        self.calls = {"categories": 0, "rows": 0, "attributes": 0}

    def load_categories(self):
        self.calls["categories"] += 1
        return [dict(row) for row in self.categories]

    def load_category_attribute_rows(self, category_names):
        self.calls["rows"] += 1
        return [dict(row) for row in self.rows if row.get("parent") in category_names]

    def load_attributes(self, attribute_names):
        self.calls["attributes"] += 1
        return {name: dict(self.attributes[name]) for name in attribute_names if name in self.attributes}


def category(name, *, parent=None, group=0, active=1, order=0, service=0):
    return {
        "name": name,
        "category_name": name,
        "parent_aos_category": parent,
        "sort_order": order,
        "is_group": group,
        "is_active": active,
        "is_service": service,
        "pricing_requirement": "Optional",
        "allowed_price_types": "Fixed\nNegotiable",
        "allowed_price_units": "hour" if service else "",
        "icon": "",
        "icon_media": None,
    }


class TestCatalogService(TestCase):
    def test_public_tree_is_deterministic(self):
        repo = FakeCatalogRepository(
            [
                category("B Root", group=1, order=2),
                category("A Root", group=1, order=1),
                category("Leaf Z", parent="A Root", order=2),
                category("Leaf A", parent="A Root", order=1),
            ]
        )
        tree = CatalogService(repo).list_public_categories()
        self.assertEqual([item["id"] for item in tree], ["A Root", "B Root"])
        self.assertEqual([item["id"] for item in tree[0]["children"]], ["Leaf A", "Leaf Z"])

    def test_inactive_parent_hides_active_child(self):
        repo = FakeCatalogRepository(
            [category("Root", group=1, active=0), category("Leaf", parent="Root", active=1)]
        )
        self.assertEqual(CatalogService(repo).list_public_categories(), [])

    def test_cycle_fails_closed(self):
        repo = FakeCatalogRepository(
            [category("A", parent="B"), category("B", parent="A")]
        )
        with self.assertRaises(CatalogDataError):
            CatalogService(repo).list_public_categories()

    def test_missing_schema_category_is_not_found(self):
        with self.assertRaises(CatalogNotFoundError):
            CatalogService(FakeCatalogRepository([])).get_public_schema("Missing")

    def test_inactive_schema_category_is_not_found(self):
        repo = FakeCatalogRepository([category("Hidden", active=0)])
        with self.assertRaises(CatalogNotFoundError):
            CatalogService(repo).get_public_schema("Hidden")

    def test_missing_parent_is_hidden_from_tree(self):
        repo = FakeCatalogRepository([category("Orphan", parent="Missing")])
        self.assertEqual(CatalogService(repo).list_public_categories(), [])

    def test_group_is_not_sellable(self):
        service = CatalogService(FakeCatalogRepository([category("Root", group=1)]))
        with self.assertRaises(CatalogValidationError) as exc:
            service.assert_sellable_category("Root")
        self.assertEqual(exc.exception.code, "CATEGORY_NOT_SELLABLE")

    def test_leaf_is_sellable_only_with_active_ancestry(self):
        service = CatalogService(
            FakeCatalogRepository([category("Root", group=1), category("Leaf", parent="Root")])
        )
        self.assertEqual(service.assert_sellable_category("Leaf"), "Leaf")

    def test_schema_uses_bounded_bulk_queries(self):
        repo = FakeCatalogRepository(
            [category("Root", group=1), category("Leaf", parent="Root")],
            rows=[
                {
                    "name": "ROW-1",
                    "parent": "Root",
                    "idx": 1,
                    "attribute": "Condition",
                    "sort_order": 1,
                    "options_override": "New\nUsed",
                    "is_required": 1,
                    "is_active": 1,
                }
            ],
            attributes={
                "Condition": {
                    "name": "Condition",
                    "label": "Condition",
                    "field_type": "Select",
                    "unit": "",
                    "help_text": "",
                    "options": "",
                    "is_active": 1,
                }
            },
        )
        schema = CatalogService(repo).get_public_schema("Leaf")
        self.assertEqual(schema["attributes"][0]["options"], ["New", "Used"])
        self.assertEqual(repo.calls, {"categories": 1, "rows": 1, "attributes": 1})


    def test_missing_attribute_definition_fails_closed(self):
        repo = FakeCatalogRepository(
            [category("Leaf")],
            rows=[
                {
                    "name": "ROW-1",
                    "parent": "Leaf",
                    "idx": 1,
                    "attribute": "Missing",
                    "sort_order": 1,
                    "options_override": "",
                    "is_required": 0,
                    "is_active": 1,
                }
            ],
        )
        with self.assertRaises(CatalogDataError):
            CatalogService(repo).get_public_schema("Leaf")

    def test_ambiguous_public_attribute_keys_fail_closed(self):
        definitions = {
            "Fuel Type": {
                "name": "Fuel Type",
                "label": "Fuel Type",
                "field_type": "Text",
                "unit": "",
                "help_text": "",
                "options": "",
                "is_active": 1,
            },
            "Fuel-Type": {
                "name": "Fuel-Type",
                "label": "Fuel-Type",
                "field_type": "Text",
                "unit": "",
                "help_text": "",
                "options": "",
                "is_active": 1,
            },
        }
        chain = [
            {
                "name": "Leaf",
                "attributes": [
                    {"name": "1", "idx": 1, "attribute": "Fuel Type", "sort_order": 1, "is_required": 0, "is_active": 1, "options_override": ""},
                    {"name": "2", "idx": 2, "attribute": "Fuel-Type", "sort_order": 2, "is_required": 0, "is_active": 1, "options_override": ""},
                ],
                "attribute_definitions": definitions,
            }
        ]
        with self.assertRaises(CatalogDataError):
            resolve_attributes(chain)

    def test_leaf_can_reenable_parent_disabled_attribute(self):
        definitions = {
            "Brand": {
                "name": "Brand",
                "label": "Brand",
                "field_type": "Text",
                "unit": "",
                "help_text": "",
                "options": "",
                "is_active": 1,
            }
        }
        chain = [
            {
                "name": "Leaf",
                "attributes": [
                    {
                        "name": "L",
                        "idx": 1,
                        "attribute": "Brand",
                        "sort_order": 1,
                        "is_required": 1,
                        "is_active": 1,
                        "options_override": "",
                    }
                ],
                "attribute_definitions": definitions,
            },
            {
                "name": "Root",
                "attributes": [
                    {
                        "name": "R",
                        "idx": 1,
                        "attribute": "Brand",
                        "sort_order": 1,
                        "is_required": 0,
                        "is_active": 0,
                        "options_override": "",
                    }
                ],
                "attribute_definitions": definitions,
            },
        ]
        attributes = resolve_attributes(chain)
        self.assertEqual(len(attributes), 1)
        self.assertEqual(attributes[0]["required"], 1)

    def test_legacy_duplicate_rows_resolve_last_row_deterministically(self):
        definitions = {
            "Brand": {
                "name": "Brand",
                "label": "Brand",
                "field_type": "Text",
                "unit": "",
                "help_text": "",
                "options": "",
                "is_active": 1,
            }
        }
        chain = [
            {
                "name": "Leaf",
                "attributes": [
                    {"name": "1", "idx": 1, "attribute": "Brand", "sort_order": 1, "is_required": 0, "is_active": 1, "options_override": ""},
                    {"name": "2", "idx": 2, "attribute": "Brand", "sort_order": 1, "is_required": 1, "is_active": 1, "options_override": ""},
                ],
                "attribute_definitions": definitions,
            }
        ]
        self.assertEqual(resolve_attributes(chain)[0]["required"], 1)

    def test_service_pricing_includes_units_only_for_services(self):
        goods = [category("Goods")]
        service = [category("Service", service=1)]
        self.assertNotIn("allowed_price_units", resolve_pricing(goods))
        self.assertEqual(resolve_pricing(service)["allowed_price_units"], ["hour"])

    def test_group_filter_expands_only_active_leaf_children(self):
        repo = FakeCatalogRepository(
            [
                category("Root", group=1),
                category("Leaf A", parent="Root", active=1, order=2),
                category("Leaf B", parent="Root", active=1, order=1),
                category("Hidden", parent="Root", active=0),
            ]
        )
        self.assertEqual(CatalogService(repo).resolve_filter_values("Root"), ["Leaf B", "Leaf A"])
