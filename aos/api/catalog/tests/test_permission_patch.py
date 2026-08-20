from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from aos.patches.v1_0 import enforce_catalog_desk_permissions


class TestCatalogPermissionPatch(TestCase):
    def test_patch_preserves_role_permission_manager_overrides(self):
        with (
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

        sql.assert_not_called()
        commit.assert_not_called()
        self.assertEqual(
            [call.kwargs["doctype"] for call in clear_cache.call_args_list],
            list(enforce_catalog_desk_permissions.CATALOG_ROLE_MANAGED_DOCTYPES),
        )
        logger.assert_called_once()
