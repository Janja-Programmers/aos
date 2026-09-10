from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from aos.patches.v1_0 import harden_media_subsystem


class TestMediaSchemaIndexPatch(TestCase):
    def test_media_and_processing_indexes_use_frappe_ddl_safe_api(self):
        def table_exists(doctype):
            return doctype in {
                harden_media_subsystem.MEDIA_DOCTYPE,
                harden_media_subsystem.PROCESSING_DOCTYPE,
            }

        with (
            patch.object(harden_media_subsystem.frappe.db, "table_exists", side_effect=table_exists),
            patch.object(harden_media_subsystem.frappe.db, "add_index") as add_index,
            patch.object(harden_media_subsystem.frappe, "logger") as logger,
        ):
            harden_media_subsystem.execute()

        expected = len(harden_media_subsystem.MEDIA_INDEXES) + len(harden_media_subsystem.PROCESSING_INDEXES)
        self.assertEqual(add_index.call_count, expected)
        for call in add_index.call_args_list:
            self.assertIsInstance(call.args[1], list)
            self.assertTrue(str(call.kwargs["index_name"]).startswith("idx_aos_media_"))
        logger.assert_called_once_with("aos.media", allow_site=True)

    def test_missing_media_tables_are_a_noop(self):
        with (
            patch.object(harden_media_subsystem.frappe.db, "table_exists", return_value=False),
            patch.object(harden_media_subsystem.frappe.db, "add_index") as add_index,
        ):
            harden_media_subsystem.execute()
        add_index.assert_not_called()
