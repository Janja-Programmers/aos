from __future__ import annotations

from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[4]
CATALOG_DOC = ROOT / "docs" / "features" / "catalog" / "README.md"


class TestCatalogDocumentationContract(TestCase):
    def test_catalog_has_one_authoritative_feature_document(self):
        catalog_dir = CATALOG_DOC.parent
        self.assertEqual(sorted(path.name for path in catalog_dir.glob("*.md")), ["README.md"])

    def test_document_covers_media_concurrency_indexes_and_frontend_contract(self):
        text = CATALOG_DOC.read_text()
        required = (
            "category_icon",
            "image_media",
            "image_url",
            "AOS Category Attribute Row",
            "attribute_key",
            "uq_catalog_category_attribute",
            "idx_catalog_attribute_category_reference",
            "idx_catalog_ad_category_reference",
            "after_migrate",
            "FOR UPDATE",
            "get_categories",
            "get_category_schema",
            "get_attribute_options",
            "AOS Category Attribute Dependency Row",
            "depends_on_attribute",
            "child_options",
            "Small Text",
            "uq_catalog_dependency_mapping",
            "aos:catalog:v5",
            "Brand → Model → Variant",
            "INVALID_CATALOG_INPUT",
            "CATEGORY_IN_USE",
            "ATTRIBUTE_IN_USE",
            "Ads create/edit",
        )
        for token in required:
            self.assertIn(token, text)
