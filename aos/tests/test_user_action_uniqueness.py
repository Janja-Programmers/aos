from __future__ import annotations

import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.live.tracking import track_join_impl
from aos.api.notifications.token import register_push_token_impl
from aos.api.social.block import block_user_impl
from aos.patches.v1_0.add_unique_constraints import USER_ACTION_UNIQUE_CONSTRAINTS
from aos.services.accounts.identity import ensure_public_account_id
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestUserActionUniqueness(AOSFeatureTestMixin, FrappeTestCase):
    """Race-sensitive uniqueness tests with transaction-local fixtures."""

    def setUp(self):
        self.prefix = f"unique-{uuid.uuid4().hex[:10]}"
        self.created_users: list[str] = []
        self.created_live_names: list[str] = []
        self.created_short_names: list[str] = []
        frappe.set_user("Administrator")

    def tearDown(self):
        # MariaDB fixtures are deliberately never committed. FrappeTestCase rolls
        # them back. Redis hot state is external to that transaction and must be
        # removed explicitly before a generated id can be reused by another test.
        for live_id in self.created_live_names:
            self._clear_live_ephemeral_state(live_id)
        for short_id in self.created_short_names:
            self._clear_short_ephemeral_state(short_id)
        frappe.set_user("Administrator")
        self.restore_localization_test_state()
        super().tearDown()

    def test_unique_indexes_exist(self):
        # Tests must verify migration-owned schema; they must not install/commit
        # production indexes themselves as part of fixture setup.
        for index in USER_ACTION_UNIQUE_CONSTRAINTS:
            with self.subTest(index=index["constraint_name"]):
                self.assertTrue(
                    self._unique_index_exists(
                        doctype=index["doctype"],
                        constraint_name=index["constraint_name"],
                    ),
                    index["constraint_name"],
                )

    def test_block_user_double_request_keeps_one_active_block(self):
        blocker = self.make_user("blocker")
        blocked = self.make_user("blocked")
        frappe.set_user(blocker)

        first = block_user_impl(account_id=ensure_public_account_id(blocked), reason="spam")
        second = block_user_impl(account_id=ensure_public_account_id(blocked), reason="spam again")

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(
            frappe.db.count(
                "AOS User Block",
                {"blocker_user": blocker, "blocked_user": blocked, "status": "Active"},
            ),
            1,
        )

    def test_live_track_join_double_request_keeps_one_active_view(self):
        host = self.make_user("host")
        viewer = self.make_user("viewer")
        live = self.make_live(host=host)
        session_id = f"{self.prefix}-live-session"
        frappe.set_user(viewer)

        first = track_join_impl(live_id=live.name, session_id=session_id)
        second = track_join_impl(live_id=live.name, session_id=session_id)

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(
            frappe.db.count(
                "AOS Live Stream View",
                {"live_stream": live.name, "session_id": session_id, "is_active": 1},
            ),
            1,
        )

    def test_push_token_double_registration_keeps_one_active_device_token(self):
        user = self.make_user("push")
        frappe.set_user(user)
        device_id = f"{self.prefix}-device"

        first = register_push_token_impl(
            token=f"{self.prefix}-token-1",
            device_type="android",
            device_id=device_id,
            registration_kind="token",
        )
        second = register_push_token_impl(
            token=f"{self.prefix}-token-2",
            device_type="android",
            device_id=device_id,
            registration_kind="token",
        )

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(
            frappe.db.count(
                "AOS Push Token",
                {"user": user, "device_id": device_id, "is_active": 1},
            ),
            1,
        )

    def test_comment_like_unique_constraint_blocks_duplicate_rows(self):
        user = self.make_user("commenter")
        short = self.make_short(owner=user)
        comment = self._make_comment(short.name, user)
        frappe.set_user(user)

        frappe.get_doc(
            {"doctype": "AOS Short Comment Like", "comment": comment.name, "user": user}
        ).insert(ignore_permissions=True)

        savepoint = f"duplicate_short_comment_like_{uuid.uuid4().hex[:12]}"
        frappe.db.savepoint(savepoint)
        with self.assertRaises(Exception):
            frappe.get_doc(
                {"doctype": "AOS Short Comment Like", "comment": comment.name, "user": user}
            ).insert(ignore_permissions=True)
        frappe.db.rollback(save_point=savepoint)

        self.assertEqual(
            frappe.db.count("AOS Short Comment Like", {"comment": comment.name, "user": user}),
            1,
        )

    def _make_comment(self, short_id: str, user: str):
        frappe.set_user(user)
        return frappe.get_doc(
            {
                "doctype": "AOS Short Comment",
                "short": short_id,
                "user": user,
                "comment": "Duplicate protection test comment",
                "status": "active",
            }
        ).insert(ignore_permissions=True)

    @staticmethod
    def _unique_index_exists_static(*, doctype: str, constraint_name: str) -> bool:
        return bool(
            frappe.db.sql(
                """
                SELECT INDEX_NAME
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                  AND INDEX_NAME = %s
                  AND NON_UNIQUE = 0
                LIMIT 1
                """,
                (f"tab{doctype}", constraint_name),
                as_dict=True,
            )
        )

    def _unique_index_exists(self, *, doctype: str, constraint_name: str) -> bool:
        return self._unique_index_exists_static(
            doctype=doctype,
            constraint_name=constraint_name,
        )
