from __future__ import annotations

from unittest import TestCase

from aos.services.catalog.errors import CatalogDataError, CatalogNotFoundError, CatalogValidationError
from aos.services.catalog.service import CatalogService, resolve_attributes, resolve_pricing


class FakeCatalogRepository:
    def __init__(self, categories, rows=None, attributes=None):
        self.categories = categories
        self.rows = rows or []
        self.attributes = attributes or {}
        self.calls = {"category": 0, "categories": 0, "rows": 0, "attributes": 0}

    def load_category(self, category_id):
        self.calls["category"] += 1
        for row in self.categories:
            if row.get("name") == category_id:
                return dict(row)
        return None

    def load_categories(self):
        self.calls["categories"] += 1
        return [dict(row) for row in self.categories]

    def load_category_attribute_rows(self, category_names):
        self.calls["rows"] += 1
        return [dict(row) for row in self.rows if row.get("parent") in category_names]

    def load_attributes(self, attribute_names):
        self.calls["attributes"] += 1
        return {name: dict(self.attributes[name]) for name in attribute_names if name in self.attributes}


class FakeMediaService:
    def __init__(self, urls=None):
        self.urls = urls or {}
        self.calls = []

    def get_public_attachment_url_map(
        self, attachments, *, purpose, attached_doctype, attached_field
    ):
        expected = list(attachments)
        self.calls.append((expected, purpose, attached_doctype, attached_field))
        return {key: self.urls[key] for key in expected if key in self.urls}


def category(name, *, parent=None, group=0, active=1, order=0, service=0, image_media=None):
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
        "image_media": image_media,
    }


def attribute_definition(name="Condition", *, key="condition", field_type="Select", options=""):
    return {
        "name": name,
        "attribute_key": key,
        "label": name,
        "field_type": field_type,
        "unit": "",
        "help_text": "",
        "options": options,
        "is_active": 1,
    }


class TestCatalogService(TestCase):
    def test_public_tree_is_deterministic_and_projects_only_media_url(self):
        repo = FakeCatalogRepository(
            [
                category("B Root", group=1, order=2),
                category("A Root", group=1, order=1, image_media="MEDIA-A"),
                category("Leaf Z", parent="A Root", order=2),
                category("Leaf A", parent="A Root", order=1),
            ]
        )
        media = FakeMediaService({("MEDIA-A", "A Root"): "https://cdn.example.test/a.webp"})
        tree = CatalogService(repo, media_service=media).list_public_categories()

        self.assertEqual([item["id"] for item in tree], ["A Root", "B Root"])
        self.assertEqual([item["id"] for item in tree[0]["children"]], ["Leaf A", "Leaf Z"])
        self.assertEqual(tree[0]["image_url"], "https://cdn.example.test/a.webp")
        self.assertNotIn("image_media", tree[0])
        self.assertNotIn("icon", tree[0])
        self.assertEqual(media.calls, [([("MEDIA-A", "A Root")], "category_icon", "AOS Category", "image_media")])

    def test_inactive_parent_hides_active_child(self):
        repo = FakeCatalogRepository(
            [category("Root", group=1, active=0), category("Leaf", parent="Root", active=1)]
        )
        self.assertEqual(CatalogService(repo).list_public_categories(), [])

    def test_missing_parent_is_hidden_from_public_tree(self):
        self.assertEqual(
            CatalogService(FakeCatalogRepository([category("Orphan", parent="Missing")])).list_public_categories(),
            [],
        )

    def test_cycle_fails_closed(self):
        repo = FakeCatalogRepository([category("A", parent="B"), category("B", parent="A")])
        with self.assertRaises(CatalogDataError):
            CatalogService(repo).list_public_categories()

    def test_group_with_parent_fails_closed_even_if_parent_missing(self):
        repo = FakeCatalogRepository([category("Broken Group", parent="Missing", group=1)])
        with self.assertRaises(CatalogDataError):
            CatalogService(repo).list_public_categories()

    def test_missing_or_inactive_schema_category_is_not_found(self):
        with self.assertRaises(CatalogNotFoundError):
            CatalogService(FakeCatalogRepository([])).get_public_schema("Missing")
        with self.assertRaises(CatalogNotFoundError):
            CatalogService(FakeCatalogRepository([category("Hidden", active=0)])).get_public_schema("Hidden")

    def test_group_is_not_sellable(self):
        service = CatalogService(FakeCatalogRepository([category("Root", group=1)]))
        with self.assertRaises(CatalogValidationError) as exc:
            service.assert_sellable_category("Root")
        self.assertEqual(exc.exception.code, "CATEGORY_NOT_SELLABLE")

    def test_schema_uses_bounded_bulk_queries_and_stored_attribute_key(self):
        repo = FakeCatalogRepository(
            [category("Root", group=1), category("Leaf", parent="Root", image_media="MEDIA-LEAF")],
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
            attributes={"Condition": attribute_definition(key="listing_condition")},
        )
        media = FakeMediaService({("MEDIA-LEAF", "Leaf"): "https://cdn.example.test/leaf.webp"})
        schema = CatalogService(repo, media_service=media).get_public_schema("Leaf")

        self.assertEqual(schema["attributes"][0]["key"], "listing_condition")
        self.assertEqual(schema["attributes"][0]["options"], ["New", "Used"])
        self.assertEqual(schema["category"]["image_url"], "https://cdn.example.test/leaf.webp")
        self.assertEqual(schema["pricing"]["requirement"], "Optional")
        self.assertEqual(schema["pricing"]["allowed_price_types"], ["Fixed", "Negotiable"])
        self.assertEqual(repo.calls, {"category": 2, "categories": 0, "rows": 1, "attributes": 1})

    def test_non_select_category_options_fail_closed(self):
        definitions = {"Weight": attribute_definition("Weight", key="weight", field_type="Number")}
        chain = [
            {
                "name": "Leaf",
                "attributes": [
                    {
                        "name": "ROW-1",
                        "idx": 1,
                        "attribute": "Weight",
                        "sort_order": 1,
                        "is_required": 0,
                        "is_active": 1,
                        "options_override": "1\n2",
                    }
                ],
                "attribute_definitions": definitions,
            }
        ]
        with self.assertRaises(CatalogDataError):
            resolve_attributes(chain)

    def test_duplicate_relation_rows_fail_closed_instead_of_last_row_wins(self):
        definitions = {"Brand": attribute_definition("Brand", key="brand", field_type="Text")}
        chain = [
            {
                "name": "Leaf",
                "attributes": [
                    {"name": "1", "idx": 1, "attribute": "Brand", "sort_order": 1, "is_required": 0, "is_active": 1, "options_override": ""},
                    {"name": "2", "idx": 2, "attribute": "Brand", "sort_order": 2, "is_required": 1, "is_active": 1, "options_override": ""},
                ],
                "attribute_definitions": definitions,
            }
        ]
        with self.assertRaises(CatalogDataError):
            resolve_attributes(chain)

    def test_missing_attribute_key_or_definition_fails_closed(self):
        repo = FakeCatalogRepository(
            [category("Leaf")],
            rows=[{"name": "ROW", "parent": "Leaf", "idx": 1, "attribute": "Missing", "sort_order": 0, "options_override": "", "is_required": 0, "is_active": 1}],
        )
        with self.assertRaises(CatalogDataError):
            CatalogService(repo).get_public_schema("Leaf")

        definition = attribute_definition("Brand", key="", field_type="Text")
        chain = [{"name": "Leaf", "attributes": [{"name": "R", "idx": 1, "attribute": "Brand", "sort_order": 0, "is_required": 0, "is_active": 1, "options_override": ""}], "attribute_definitions": {"Brand": definition}}]
        with self.assertRaises(CatalogDataError):
            resolve_attributes(chain)

    def test_leaf_override_wins_and_order_is_deterministic(self):
        definitions = {"Brand": attribute_definition("Brand", key="brand", field_type="Select", options="A\nB")}
        chain = [
            {"name": "Leaf", "attributes": [{"name": "L", "idx": 1, "attribute": "Brand", "sort_order": 20, "is_required": 1, "is_active": 1, "options_override": "B\nC"}], "attribute_definitions": definitions},
            {"name": "Root", "attributes": [{"name": "R", "idx": 1, "attribute": "Brand", "sort_order": 1, "is_required": 0, "is_active": 1, "options_override": "A\nB"}], "attribute_definitions": definitions},
        ]
        item = resolve_attributes(chain)[0]
        self.assertEqual(item["options"], ["B", "C"])
        self.assertEqual(item["required"], 1)
        self.assertEqual(item["sort_order"], 20)

    def test_pricing_resolution_is_leaf_only_and_validated(self):
        leaf = category("Service", service=1)
        leaf["pricing_requirement"] = "Required"
        leaf["allowed_price_types"] = "Fixed\nNegotiable"
        leaf["allowed_price_units"] = "Per hour\nPer job"
        result = resolve_pricing([leaf])
        self.assertEqual(result["pricing_requirement"], "Required")
        self.assertEqual(result["allowed_price_units"], ["Per hour", "Per job"])

    def test_filter_values_are_bounded_to_active_sellable_children(self):
        repo = FakeCatalogRepository(
            [
                category("Root", group=1),
                category("Leaf B", parent="Root", order=1),
                category("Leaf A", parent="Root", order=2),
                category("Hidden", parent="Root", active=0),
            ]
        )
        self.assertEqual(CatalogService(repo).resolve_filter_values("Root"), ["Leaf B", "Leaf A"])
