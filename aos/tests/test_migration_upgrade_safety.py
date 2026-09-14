from __future__ import annotations

import importlib
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate, now_datetime

from aos.services.sellers import schema as seller_schema
from aos.patches.v1_0.add_unique_constraints import (
    UNIQUE_CONSTRAINTS,
    execute as apply_unique_constraints,
)
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestMigrationUpgradeSafety(FrappeTestCase, AOSFeatureTestMixin):
    """Migration/upgrade safety tests for production-critical patches.

    These tests intentionally simulate partially migrated legacy data by
    inserting rows through SQL with the newer hidden uniqueness keys left NULL.
    MariaDB permits multiple NULL values in unique indexes, so this is a safe
    way to verify duplicate cleanup without dropping production indexes.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        apply_unique_constraints()
        seller_schema.execute()
        frappe.db.commit()

    def setUp(self):
        self.prefix = self.make_prefix("migration")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")
        self.cleanup_feature_rows()
        frappe.db.commit()

    def test_patches_txt_entries_are_importable_and_executable(self):
        patch_file = Path(frappe.get_app_path("aos", "patches.txt"))
        patch_modules = [
            line.strip()
            for line in patch_file.read_text().splitlines()
            if line.strip()
            and not line.strip().startswith("#")
            and not line.strip().startswith("[")
        ]

        self.assertIn("aos.patches.v1_0.add_unique_constraints", patch_modules)

        for module_name in patch_modules:
            with self.subTest(patch=module_name):
                module = importlib.import_module(module_name)
                self.assertTrue(callable(getattr(module, "execute", None)))

    def test_required_fields_and_indexes_exist_after_migrate(self):
        required_fields = {
            "AOS Live Stream View": ["active_identity_key"],
            "AOS Short View": ["identity_key"],
            "AOS Push Token": ["token_hash", "active_device_key", "registration_kind"],
        }

        for doctype, fields in required_fields.items():
            with self.subTest(doctype=doctype):
                self.assertTrue(frappe.db.exists("DocType", doctype), doctype)
                for field in fields:
                    self.assertTrue(
                        frappe.db.has_column(doctype, field),
                        f"{doctype}.{field}",
                    )

        for constraint in UNIQUE_CONSTRAINTS:
            with self.subTest(index=constraint["constraint_name"]):
                self.assertTrue(
                    self._index_exists(
                        doctype=constraint["doctype"],
                        index_name=constraint["constraint_name"],
                        unique_only=True,
                    ),
                    constraint["constraint_name"],
                )

        for index_name, _fields in seller_schema.SELLER_INDEXES:
            with self.subTest(index=index_name):
                self.assertTrue(
                    self._index_exists(
                        doctype="AOS Seller",
                        index_name=index_name,
                        unique_only=False,
                    ),
                    index_name,
                )

    def test_index_patches_are_idempotent_on_existing_site(self):
        before = self._existing_indexes_snapshot()

        apply_unique_constraints()
        seller_schema.execute()
        apply_unique_constraints()
        seller_schema.execute()

        after = self._existing_indexes_snapshot()
        self.assertEqual(before, after)


    def test_live_view_legacy_duplicate_cleanup_is_idempotent(self):
        host = self.make_user("live-host")
        viewer = self.make_user("live-viewer")
        live = self.make_live(host=host)
        session_id = f"{self.prefix}-session"

        self._insert_legacy_live_view("first", live.name, viewer, session_id)
        self._insert_legacy_live_view("second", live.name, viewer, session_id)

        apply_unique_constraints()
        apply_unique_constraints()

        rows = frappe.get_all(
            "AOS Live Stream View",
            filters={"live_stream": live.name, "session_id": session_id},
            fields=["name", "is_active", "active_identity_key", "left_at"],
            order_by="is_active desc, name asc",
        )

        self.assertEqual(len(rows), 2)
        active_rows = [row for row in rows if int(row.is_active or 0) == 1]
        inactive_rows = [row for row in rows if int(row.is_active or 0) == 0]
        self.assertEqual(len(active_rows), 1)
        self.assertEqual(len(inactive_rows), 1)
        self.assertEqual(active_rows[0].active_identity_key, session_id)
        self.assertFalse(inactive_rows[0].active_identity_key)
        self.assertTrue(inactive_rows[0].left_at)

    def test_short_view_legacy_duplicate_cleanup_is_idempotent(self):
        viewer = self.make_user("short-viewer")
        short = self.make_short(owner=viewer)
        view_date = getdate()

        self._insert_legacy_short_view(
            "first",
            short.name,
            viewer,
            view_date,
            watch_ms=1000,
            qualified=0,
        )
        self._insert_legacy_short_view(
            "second",
            short.name,
            viewer,
            view_date,
            watch_ms=8000,
            qualified=1,
        )

        apply_unique_constraints()
        apply_unique_constraints()

        rows = frappe.get_all(
            "AOS Short View",
            filters={"short": short.name, "user": viewer, "view_date": view_date},
            fields=["name", "identity_key", "watch_ms", "qualified"],
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].identity_key, f"user:{viewer}")
        self.assertEqual(int(rows[0].watch_ms or 0), 8000)
        self.assertEqual(int(rows[0].qualified or 0), 1)
        self.assertEqual(
            int(frappe.db.get_value("AOS Short", short.name, "view_count") or 0),
            1,
        )

    # RAW LEGACY FIXTURES


    def _insert_legacy_live_view(self, label: str, live: str, user: str, session_id: str):
        now = now_datetime()
        frappe.db.sql(
            """
            INSERT INTO `tabAOS Live Stream View`
                (name, creation, modified, modified_by, owner, docstatus, idx,
                 live_stream, user, joined_at, last_seen_at, session_id,
                 watch_duration_seconds, qualified, is_active, active_identity_key)
            VALUES
                (%s, %s, %s, 'Administrator', 'Administrator', 0, 0,
                 %s, %s, %s, %s, %s,
                 0, 0, 1, NULL)
            """,
            (f"{self.prefix}-{label}-live-view", now, now, live, user, now, now, session_id),
        )

    def _insert_legacy_short_view(
        self,
        label: str,
        short: str,
        user: str,
        view_date,
        *,
        watch_ms: int,
        qualified: int,
    ):
        now = now_datetime()
        frappe.db.sql(
            """
            INSERT INTO `tabAOS Short View`
                (name, creation, modified, modified_by, owner, docstatus, idx,
                 short, view_date, user, qualified, watch_ms, last_seen_at, identity_key)
            VALUES
                (%s, %s, %s, 'Administrator', 'Administrator', 0, 0,
                 %s, %s, %s, %s, %s, %s, NULL)
            """,
            (
                f"{self.prefix}-{label}-short-view",
                now,
                now,
                short,
                view_date,
                user,
                qualified,
                watch_ms,
                now,
            ),
        )

    # DB INTROSPECTION

    @staticmethod
    def _index_exists(*, doctype: str, index_name: str, unique_only: bool) -> bool:
        unique_clause = "AND NON_UNIQUE = 0" if unique_only else ""
        return bool(
            frappe.db.sql(
                f"""
                SELECT INDEX_NAME
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                  AND INDEX_NAME = %s
                  {unique_clause}
                LIMIT 1
                """,
                (f"tab{doctype}", index_name),
                as_dict=True,
            )
        )

    @staticmethod
    def _existing_indexes_snapshot() -> set[tuple[str, str, int]]:
        index_names = [
            *(constraint["constraint_name"] for constraint in UNIQUE_CONSTRAINTS),
            *(index_name for index_name, _fields in seller_schema.SELLER_INDEXES),
        ]
        rows = frappe.db.sql(
            """
            SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND INDEX_NAME IN %(index_names)s
            GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE
            """,
            {"index_names": tuple(index_names)},
            as_dict=True,
        )
        return {
            (str(row.TABLE_NAME), str(row.INDEX_NAME), int(row.NON_UNIQUE or 0))
            for row in rows
        }
