from __future__ import annotations

import hashlib
import importlib
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate, now_datetime

from aos.patches.v1_0 import add_seller_location_indexes
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
        add_seller_location_indexes.execute()
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
        self.assertIn("aos.patches.v1_0.add_seller_location_indexes", patch_modules)

        for module_name in patch_modules:
            with self.subTest(patch=module_name):
                module = importlib.import_module(module_name)
                self.assertTrue(callable(getattr(module, "execute", None)))

    def test_required_fields_and_indexes_exist_after_migrate(self):
        required_fields = {
            "AOS User Block": ["active_pair_key"],
            "AOS Live Stream View": ["active_identity_key"],
            "AOS Short View": ["identity_key"],
            "AOS Push Token": ["token_hash", "active_device_key"],
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

        for index in add_seller_location_indexes.INDEXES:
            with self.subTest(index=index["name"]):
                self.assertTrue(
                    self._index_exists(
                        doctype="AOS Seller",
                        index_name=index["name"],
                        unique_only=False,
                    ),
                    index["name"],
                )

    def test_index_patches_are_idempotent_on_existing_site(self):
        before = self._existing_indexes_snapshot()

        apply_unique_constraints()
        add_seller_location_indexes.execute()
        apply_unique_constraints()
        add_seller_location_indexes.execute()

        after = self._existing_indexes_snapshot()
        self.assertEqual(before, after)

    def test_user_block_legacy_duplicate_cleanup_is_idempotent(self):
        blocker = self.make_user("blocker")
        blocked = self.make_user("blocked")

        self._insert_legacy_user_block("first", blocker, blocked)
        self._insert_legacy_user_block("second", blocker, blocked)

        apply_unique_constraints()
        apply_unique_constraints()

        rows = frappe.get_all(
            "AOS User Block",
            filters={"blocker_user": blocker, "blocked_user": blocked},
            fields=["name", "status", "active_pair_key"],
            order_by="status asc, name asc",
        )

        self.assertEqual(len(rows), 2)
        active_rows = [row for row in rows if row.status == "Active"]
        inactive_rows = [row for row in rows if row.status == "Unblocked"]
        self.assertEqual(len(active_rows), 1)
        self.assertEqual(len(inactive_rows), 1)
        self.assertEqual(active_rows[0].active_pair_key, f"{blocker}|{blocked}")
        self.assertFalse(inactive_rows[0].active_pair_key)

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

    def test_push_token_legacy_duplicate_cleanup_is_idempotent(self):
        user = self.make_user("push")
        device_id = f"{self.prefix}-device"

        self._insert_legacy_push_token("first", user, device_id, token="token-one")
        self._insert_legacy_push_token("second", user, device_id, token="token-two")

        apply_unique_constraints()
        apply_unique_constraints()

        rows = frappe.get_all(
            "AOS Push Token",
            filters={"user": user, "device_id": device_id},
            fields=["name", "is_active", "token_hash", "active_device_key"],
            order_by="is_active desc, name asc",
        )

        self.assertEqual(len(rows), 2)
        active_rows = [row for row in rows if int(row.is_active or 0) == 1]
        inactive_rows = [row for row in rows if int(row.is_active or 0) == 0]
        self.assertEqual(len(active_rows), 1)
        self.assertEqual(len(inactive_rows), 1)
        self.assertTrue(active_rows[0].token_hash)
        self.assertEqual(active_rows[0].active_device_key, f"{user}|{device_id}")
        self.assertFalse(inactive_rows[0].active_device_key)

    # RAW LEGACY FIXTURES

    def _insert_legacy_user_block(self, label: str, blocker: str, blocked: str):
        now = now_datetime()
        frappe.db.sql(
            """
            INSERT INTO `tabAOS User Block`
                (name, creation, modified, modified_by, owner, docstatus, idx,
                 blocker_user, blocked_user, status, blocked_at, active_pair_key)
            VALUES
                (%s, %s, %s, 'Administrator', 'Administrator', 0, 0,
                 %s, %s, 'Active', %s, NULL)
            """,
            (f"{self.prefix}-{label}-block", now, now, blocker, blocked, now),
        )

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

    def _insert_legacy_push_token(self, label: str, user: str, device_id: str, *, token: str):
        now = now_datetime()
        token_value = f"{self.prefix}-{label}-{token}"
        frappe.db.sql(
            """
            INSERT INTO `tabAOS Push Token`
                (name, creation, modified, modified_by, owner, docstatus, idx,
                 user, token, token_hash, device_type, last_used_at, device_id,
                 is_active, active_device_key)
            VALUES
                (%s, %s, %s, 'Administrator', 'Administrator', 0, 0,
                 %s, %s, %s, 'android', %s, %s,
                 1, NULL)
            """,
            (
                f"{self.prefix}-{label}-push-token",
                now,
                now,
                user,
                token_value,
                hashlib.sha256(token_value.encode()).hexdigest(),
                now,
                device_id,
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
            *(index["name"] for index in add_seller_location_indexes.INDEXES),
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
