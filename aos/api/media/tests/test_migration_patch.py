from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from aos.patches.v1_0 import add_media_multipart_upload_fields, harden_media_subsystem


class TestMediaMigrationPatch(TestCase):
    def test_indexes_use_frappe_ddl_safe_api_before_data_backfill(self):
        with (
            patch.object(harden_media_subsystem.frappe.db, "table_exists", return_value=True),
            patch.object(harden_media_subsystem.frappe.db, "add_index") as add_index,
            patch.object(harden_media_subsystem.frappe.db, "sql") as sql,
            patch.object(harden_media_subsystem.frappe, "logger") as logger,
        ):
            harden_media_subsystem.execute()

        self.assertEqual(add_index.call_count, len(harden_media_subsystem.INDEXES))
        self.assertEqual(sql.call_count, 4)
        for call in add_index.call_args_list:
            self.assertEqual(call.args[0], harden_media_subsystem.DOCTYPE)
            self.assertIsInstance(call.args[1], list)
            self.assertIn(call.kwargs["index_name"], harden_media_subsystem.INDEXES)
        for call in sql.call_args_list:
            query = str(call.args[0]).lstrip().upper()
            self.assertTrue(query.startswith("UPDATE"))
            self.assertNotIn("ALTER TABLE", query)
        logger.assert_called_once_with("aos.media", allow_site=True)

    def test_missing_media_table_is_a_noop(self):
        with (
            patch.object(harden_media_subsystem.frappe.db, "table_exists", return_value=False),
            patch.object(harden_media_subsystem.frappe.db, "add_index") as add_index,
            patch.object(harden_media_subsystem.frappe.db, "sql") as sql,
        ):
            harden_media_subsystem.execute()

        add_index.assert_not_called()
        sql.assert_not_called()

    def test_multipart_upgrade_patch_backfills_legacy_rows_and_adds_active_index(self):
        with (
            patch.object(add_media_multipart_upload_fields.frappe.db, "table_exists", return_value=True),
            patch.object(add_media_multipart_upload_fields.frappe.db, "add_index") as add_index,
            patch.object(add_media_multipart_upload_fields.frappe.db, "sql") as sql,
            patch.object(add_media_multipart_upload_fields.frappe, "logger") as logger,
        ):
            add_media_multipart_upload_fields.execute()

        self.assertEqual(sql.call_count, 2)
        add_index.assert_called_once_with(
            add_media_multipart_upload_fields.DOCTYPE,
            add_media_multipart_upload_fields.INDEX_FIELDS,
            index_name=add_media_multipart_upload_fields.INDEX_NAME,
        )
        self.assertIn("upload_mode = 'direct'", str(sql.call_args_list[0].args[0]))
        logger.assert_called_once_with("aos.media", allow_site=True)
