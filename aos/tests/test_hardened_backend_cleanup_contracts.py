from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestHardenedBackendCleanupContracts(unittest.TestCase):
    _FEATURES = (
        "localization",
        "authentication",
        "media",
        "accounts",
        "notifications",
        "verification",
        "social",
        "maps",
        "sellers",
        "catalog",
        "ads",
        "search-ranking",
        "saved-search",
        "wishlist",
    )
    _REQUIRED_SECTIONS = (
        "## Overview",
        "## Responsibilities",
        "## Boundaries",
        "## Architecture",
        "## Data Model",
        "## Fields",
        "## API",
        "## Cross-feature Dependencies",
        "## Transaction / Concurrency Model",
        "## Caching",
        "## Performance / Scalability",
        "## Testing",
    )
    _DISTRIBUTED_NAMES = {
        "aos_ad": "AD",
        "aos_ad_draft": "DRAFT",
        "aos_media_object": "MEDIA",
        "aos_verification_request": "VER",
        "aos_saved_search": "SEARCH",
    }

    def test_hardened_feature_docs_are_single_authoritative_readmes(self) -> None:
        for feature in self._FEATURES:
            with self.subTest(feature=feature):
                feature_dir = ROOT / "docs" / "features" / feature
                self.assertEqual(
                    sorted(path.name for path in feature_dir.glob("*.md")),
                    ["README.md"],
                )
                text = (feature_dir / "README.md").read_text(encoding="utf-8")
                for section in self._REQUIRED_SECTIONS:
                    self.assertIn(section, text)

    def test_location_list_projection_is_visible(self) -> None:
        path = ROOT / "aos" / "aos" / "doctype" / "aos_location" / "aos_location.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in payload["fields"]}
        self.assertEqual(fields["location"].get("in_list_view"), 1)
        self.assertEqual(payload.get("autoname"), "hash")
        self.assertEqual(payload.get("naming_rule"), "Random")

    def test_high_write_names_use_distributed_safe_strategies(self) -> None:
        for module, prefix in self._DISTRIBUTED_NAMES.items():
            with self.subTest(doctype=module):
                directory = ROOT / "aos" / "aos" / "doctype" / module
                metadata = json.loads((directory / f"{module}.json").read_text(encoding="utf-8"))
                self.assertNotIn("naming_series", metadata)
                self.assertNotEqual(metadata.get("autoname"), "naming_series:")
                source = (directory / f"{module}.py").read_text(encoding="utf-8")
                self.assertIn(f'new_prefixed_name("{prefix}")', source)

        for module in ("aos_search_index_job", "aos_moderation_job", "aos_transactional_outbox"):
            with self.subTest(doctype=module):
                path = ROOT / "aos" / "aos" / "doctype" / module / f"{module}.json"
                metadata = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(metadata.get("autoname"), "hash")
                self.assertNotIn("naming_series", metadata)

        identifier_source = (ROOT / "aos" / "utils" / "identifiers.py").read_text(encoding="utf-8")
        self.assertIn("uuid4", identifier_source)

    def test_hardened_feature_documentation_has_no_parallel_api_files(self) -> None:
        for feature in ("ads", "search-ranking", "saved-search"):
            with self.subTest(feature=feature):
                self.assertFalse((ROOT / "docs" / "features" / feature / "api.md").exists())


if __name__ == "__main__":
    unittest.main()
