from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from aos.patches.v1_0 import enforce_catalog_desk_permissions


class TestCatalogPermissionPatch(TestCase):
    def test_missing_custom_permission_table_is_safe_noop(self):
        with (
            patch.object(
                enforce_catalog_desk_permissions.frappe.db,
                "table_exists",
                return_value=False,
            ),
            patch.object(enforce_catalog_desk_permissions.frappe.db, "sql") as sql,
            patch.object(enforce_catalog_desk_permissions.frappe, "clear_cache") as clear_cache,
            patch.object(enforce_catalog_desk_permissions.frappe, "logger") as logger,
        ):
            enforce_catalog_desk_permissions.execute()

        sql.assert_not_called()
        clear_cache.assert_not_called()
        logger.assert_not_called()

    def test_patch_removes_catalog_overrides_without_commit(self):
        with (
            patch.object(
                enforce_catalog_desk_permissions.frappe.db,
                "table_exists",
                return_value=True,
            ),
            patch.object(enforce_catalog_desk_permissions.frappe.db, "sql") as sql,
            patch.object(
                enforce_catalog_desk_permissions.frappe.db,
                "commit",
                create=True,
            ) as commit,
            patch.object(enforce_catalog_desk_permissions.frappe, "clear_cache") as clear_cache,
            patch.object(enforce_catalog_desk_permissions.frappe, "logger") as logger,
        ):
            enforce_catalog_desk_permissions.execute()

        sql.assert_called_once()
        statement = " ".join(str(sql.call_args.args[0]).split()).upper()
        self.assertTrue(statement.startswith("DELETE FROM `TABCUSTOM DOCPERM`"))
        self.assertIn("WHERE PARENT IN (%S, %S)", statement)
        self.assertEqual(
            sql.call_args.args[1],
            enforce_catalog_desk_permissions.CATALOG_ADMIN_DOCTYPES,
        )
        self.assertEqual(
            [call.kwargs["doctype"] for call in clear_cache.call_args_list],
            list(enforce_catalog_desk_permissions.CATALOG_ADMIN_DOCTYPES),
        )
        commit.assert_not_called()
        logger.assert_called_once()
