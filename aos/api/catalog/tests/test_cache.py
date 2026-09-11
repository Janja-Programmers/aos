from __future__ import annotations

from unittest import TestCase
from unittest.mock import Mock, patch

from aos.services.catalog import cache as catalog_cache


class TestCatalogCache(TestCase):
    def test_cache_round_trip_is_copy_isolated(self):
        backend = Mock()
        stored = {}
        backend.get_value.side_effect = lambda key: stored.get(key)
        backend.set_value.side_effect = lambda key, value, **_kwargs: stored.__setitem__(key, value)
        with patch.object(catalog_cache.frappe, "cache", return_value=backend):
            tree = [{"id": "Root", "children": []}]
            catalog_cache.set_category_tree_cache(tree)
            tree[0]["id"] = "Mutated"
            fetched = catalog_cache.get_category_tree_cache()
            self.assertEqual(fetched[0]["id"], "Root")
            fetched[0]["id"] = "Again"
            self.assertEqual(catalog_cache.get_category_tree_cache()[0]["id"], "Root")

    def test_invalidation_runs_immediately_and_after_commit(self):
        backend = Mock()
        after_commit = Mock()
        with (
            patch.object(catalog_cache.frappe, "cache", return_value=backend),
            patch.object(catalog_cache.frappe.db, "after_commit", after_commit),
        ):
            catalog_cache.clear_catalog_cache()

            backend.delete_value.assert_called_once()
            self.assertEqual(backend.delete_keys.call_count, 3)
            backend.delete_keys.assert_any_call("aos:catalog:v5:schema:*")
            backend.delete_keys.assert_any_call("aos:catalog:v5:options:*")
            backend.delete_keys.assert_any_call("aos:catalog:v5:resolved-attributes:*")
            after_commit.add.assert_called_once()
            callback = after_commit.add.call_args.args[0]
            callback()

        self.assertEqual(backend.delete_value.call_count, 2)
        self.assertEqual(backend.delete_keys.call_count, 6)

    def test_cache_failures_are_non_fatal(self):
        backend = Mock()
        backend.get_value.side_effect = RuntimeError("redis down")
        backend.set_value.side_effect = RuntimeError("redis down")
        with patch.object(catalog_cache.frappe, "cache", return_value=backend):
            self.assertIsNone(catalog_cache.get_category_tree_cache())
            catalog_cache.set_category_tree_cache([])
