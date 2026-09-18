from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from aos.patches.v1_0 import install_media_indexes


class TestMediaIndexInstaller(TestCase):
    def test_media_and_processing_indexes_use_frappe_ddl_safe_api(self):
        def table_exists(doctype):
            return doctype in {
                install_media_indexes.MEDIA_DOCTYPE,
                install_media_indexes.PROCESSING_DOCTYPE,
            }

        with (
            patch.object(install_media_indexes.frappe.db, "table_exists", side_effect=table_exists),
            patch.object(install_media_indexes, "_index_exists", return_value=False),
            patch.object(install_media_indexes.frappe.db, "add_index") as add_index,
            patch.object(install_media_indexes.frappe, "logger") as logger,
        ):
            install_media_indexes.execute()

        expected = len(install_media_indexes.MEDIA_INDEXES) + len(install_media_indexes.PROCESSING_INDEXES)
        self.assertEqual(add_index.call_count, expected)
        for call in add_index.call_args_list:
            self.assertIsInstance(call.args[1], list)
            self.assertTrue(str(call.kwargs["index_name"]).startswith("idx_aos_media_"))
        logger.assert_called_once_with("aos.media", allow_site=True)

    def test_existing_media_indexes_are_not_recreated(self):
        with (
            patch.object(install_media_indexes.frappe.db, "table_exists", return_value=True),
            patch.object(install_media_indexes, "_index_exists", return_value=True),
            patch.object(install_media_indexes.frappe.db, "add_index") as add_index,
        ):
            install_media_indexes.execute()

        add_index.assert_not_called()

    def test_missing_media_tables_are_a_noop(self):
        with (
            patch.object(install_media_indexes.frappe.db, "table_exists", return_value=False),
            patch.object(install_media_indexes.frappe.db, "add_index") as add_index,
        ):
            install_media_indexes.execute()
        add_index.assert_not_called()
