from __future__ import annotations

import ast
import unittest
from unittest.mock import patch
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.live.reactions import send_reaction_impl
from aos.api.live.messages import add_live_message_impl, delete_live_message_impl
from aos.patches.v1_0 import harden_live_subsystem, install_live_indexes
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestLiveDatabaseContracts(FrappeTestCase):
    def test_live_data_patch_is_idempotent(self):
        harden_live_subsystem.execute()
        harden_live_subsystem.execute()

    def test_live_indexes_exist_after_migrate(self):
        for doctype, name, _columns, _unique in install_live_indexes.INDEXES:
            rows = frappe.db.sql(
                """
                SELECT INDEX_NAME
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
                LIMIT 1
                """,
                (f"tab{doctype}", name),
            )
            self.assertTrue(rows, f"missing Live index {name}")

    def test_data_patch_precedes_schema_patch_and_does_not_call_livekit(self):
        patches = Path(frappe.get_app_path("aos", "patches.txt")).read_text(encoding="utf-8")
        data_patch = "aos.patches.v1_0.harden_live_subsystem"
        index_patch = "aos.patches.v1_0.install_live_indexes"
        self.assertIn(data_patch, patches)
        self.assertIn(index_patch, patches)
        self.assertLess(patches.index(data_patch), patches.index(index_patch))

        source = Path(harden_live_subsystem.__file__).read_text(encoding="utf-8")
        self.assertNotIn("LiveKitAPI", source)
        self.assertNotIn("frappe.enqueue", source)
        self.assertNotIn("frappe.db.commit", source)

    def test_index_patch_is_schema_only(self):
        tree = ast.parse(Path(install_live_indexes.__file__).read_text(encoding="utf-8"))
        forbidden: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in {"commit", "rollback", "insert", "save", "delete", "set_value"}:
                forbidden.append(f"{node.func.attr}@{node.lineno}")
            if node.func.attr == "sql" and node.args:
                query = node.args[0]
                literal = ""
                if isinstance(query, ast.Constant) and isinstance(query.value, str):
                    literal = query.value
                elif isinstance(query, ast.JoinedStr):
                    literal = "".join(
                        value.value
                        for value in query.values
                        if isinstance(value, ast.Constant) and isinstance(value.value, str)
                    )
                if literal.lstrip().upper().startswith(
                    ("INSERT", "UPDATE", "DELETE", "REPLACE", "TRUNCATE")
                ):
                    forbidden.append(f"sql-dml@{node.lineno}")
        self.assertEqual(forbidden, [])

    def test_database_uniqueness_boundaries_are_present(self):
        expected = (
            ("AOS Live Stream", "active_host_key"),
            ("AOS Live CoHost", "active_workflow_key"),
            ("AOS Live Message", "active_idempotency_key"),
            ("AOS LiveKit Webhook Event", "event_id"),
            ("AOS Notification", "dedupe_key"),
        )
        for doctype, fieldname in expected:
            field = frappe.get_meta(doctype).get_field(fieldname)
            self.assertIsNotNone(field, f"{doctype}.{fieldname}")
            self.assertTrue(field.unique, f"{doctype}.{fieldname} must be unique")

    def test_private_consistency_fields_are_not_public_desk_inputs(self):
        expected = (
            ("AOS Live Stream", "active_host_key"),
            ("AOS Live Stream View", "active_identity_key"),
            ("AOS Live Stream View", "livekit_identity"),
            ("AOS Live CoHost", "active_workflow_key"),
            ("AOS Live CoHost", "livekit_identity"),
            ("AOS Live Message", "active_idempotency_key"),
            ("AOS Notification", "dedupe_key"),
        )
        for doctype, fieldname in expected:
            field = frappe.get_meta(doctype).get_field(fieldname)
            self.assertIsNotNone(field)
            self.assertTrue(field.hidden)
            self.assertTrue(field.read_only)


class TestLiveReactionDatabaseContracts(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("live-reaction-db")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        self.cleanup_feature_rows()
        self.restore_localization_test_state()
        frappe.set_user("Administrator")

    def test_rapid_host_reactions_do_not_serialize_on_exclusive_live_lock(self):
        host = self.make_user("reaction-host")
        live = self.make_live(host=host)
        frappe.set_user(host)

        with patch("aos.api.live.reactions.rate_limit", return_value=None):
            responses = [
                send_reaction_impl(live_id=live.name, reaction_type="like")
                for _ in range(20)
            ]

        self.assertTrue(all(response.get("ok") for response in responses), responses)

        # High-frequency reactions are aggregated in Redis rather than writing
        # one MariaDB row per tap. Reconciliation materializes the exact total.
        from aos.services.live_analytics_service import LiveAnalyticsService

        self.assertEqual(
            frappe.db.count("AOS Live Stream Reaction", {"live_stream": live.name, "user": host}),
            0,
        )
        self.assertEqual(LiveAnalyticsService.sync_reaction_count(live_id=live.name), 20)
        self.assertEqual(
            int(frappe.db.get_value("AOS Live Stream", live.name, "reaction_count") or 0),
            20,
        )
        from aos.services.live.ephemeral import clear_reaction_state

        clear_reaction_state(live_id=live.name)

    def test_hot_live_redis_hashes_round_trip_through_frappe_wrapper(self):
        """Protect raw numeric hashes from RedisWrapper pickle/namespace mixing."""
        host = self.make_user("hot-state-host")
        live = self.make_live(host=host)

        from aos.services.live.ephemeral import (
            change_comment_count,
            clear_comment_state,
            clear_view_metrics,
            current_comment_count,
            current_view_metrics,
            increment_view_metrics,
        )

        base = {
            "viewer_count": 0,
            "total_views": 0,
            "total_joins": 0,
            "unique_viewers": 0,
            "peak_viewers": 0,
            "total_watch_time_seconds": 0,
        }
        joined = increment_view_metrics(live_id=live.name, base=base)
        self.assertIsNotNone(joined)
        self.assertEqual(joined["viewer_count"], 1)
        self.assertEqual(joined["total_joins"], 1)
        cached_view = current_view_metrics(live_id=live.name)
        self.assertIsNotNone(cached_view)
        self.assertEqual(cached_view["viewer_count"], 1)
        self.assertEqual(cached_view["total_joins"], 1)

        self.assertEqual(
            change_comment_count(live_id=live.name, base=0, delta=1),
            1,
        )
        self.assertEqual(current_comment_count(live_id=live.name), 1)

        clear_view_metrics(live_id=live.name)
        clear_comment_state(live_id=live.name)
        self.assertIsNone(current_view_metrics(live_id=live.name))
        self.assertIsNone(current_comment_count(live_id=live.name))


class TestLiveMessageDeleteDatabaseContracts(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("live-message-delete-db")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        self.cleanup_feature_rows()
        self.restore_localization_test_state()
        frappe.set_user("Administrator")

    def test_host_can_soft_delete_live_comment_and_counter_stays_consistent(self):
        host = self.make_user("delete-host")
        live = self.make_live(host=host)
        frappe.set_user(host)

        with patch("aos.api.live.messages.rate_limit", return_value=None):
            added = add_live_message_impl(
                live_id=live.name,
                content="remove me",
                idempotency_key=f"{self.prefix}-delete-comment",
            )
        self.assertTrue(added.get("ok"), added)
        message_id = added["data"]["message"]["message_id"]

        with patch("aos.api.live.messages.rate_limit", return_value=None):
            deleted = delete_live_message_impl(message_id=message_id)
        self.assertTrue(deleted.get("ok"), deleted)
        self.assertIn(message_id, deleted["data"]["deleted_message_ids"])
        self.assertEqual(
            frappe.db.get_value("AOS Live Message", message_id, "status"),
            "deleted",
        )
        self.assertEqual(
            int(frappe.db.get_value("AOS Live Stream", live.name, "comment_count") or 0),
            0,
        )

    def test_deleting_parent_soft_deletes_descendant_replies(self):
        host = self.make_user("thread-delete-host")
        live = self.make_live(host=host)
        frappe.set_user(host)

        with patch("aos.api.live.messages.rate_limit", return_value=None):
            root = add_live_message_impl(
                live_id=live.name,
                content="root",
                idempotency_key=f"{self.prefix}-root",
            )
        self.assertTrue(root.get("ok"), root)
        root_id = root["data"]["message"]["message_id"]

        # Use the public reply path so root_message/reply_count hooks run.
        from aos.api.live.messages import reply_live_message_impl
        with patch("aos.api.live.messages.rate_limit", return_value=None):
            reply = reply_live_message_impl(
                live_id=live.name,
                parent_message=root_id,
                content="reply",
                idempotency_key=f"{self.prefix}-reply",
            )
        self.assertTrue(reply.get("ok"), reply)
        reply_id = reply["data"]["message"]["message_id"]

        with patch("aos.api.live.messages.rate_limit", return_value=None):
            deleted = delete_live_message_impl(message_id=root_id)
        self.assertTrue(deleted.get("ok"), deleted)
        self.assertEqual(set(deleted["data"]["deleted_message_ids"]), {root_id, reply_id})
        self.assertEqual(frappe.db.get_value("AOS Live Message", root_id, "status"), "deleted")
        self.assertEqual(frappe.db.get_value("AOS Live Message", reply_id, "status"), "deleted")
        self.assertEqual(
            int(frappe.db.get_value("AOS Live Stream", live.name, "comment_count") or 0),
            0,
        )

    def test_deleting_reply_repairs_surviving_parent_reply_count(self):
        host = self.make_user("reply-delete-host")
        live = self.make_live(host=host)
        frappe.set_user(host)

        from aos.api.live.messages import reply_live_message_impl
        with patch("aos.api.live.messages.rate_limit", return_value=None):
            root = add_live_message_impl(
                live_id=live.name,
                content="root survives",
                idempotency_key=f"{self.prefix}-surviving-root",
            )
        self.assertTrue(root.get("ok"), root)
        root_id = root["data"]["message"]["message_id"]

        with patch("aos.api.live.messages.rate_limit", return_value=None):
            reply = reply_live_message_impl(
                live_id=live.name,
                parent_message=root_id,
                content="delete this reply",
                idempotency_key=f"{self.prefix}-delete-reply",
            )
        self.assertTrue(reply.get("ok"), reply)
        reply_id = reply["data"]["message"]["message_id"]
        self.assertEqual(int(frappe.db.get_value("AOS Live Message", root_id, "reply_count") or 0), 1)

        with patch("aos.api.live.messages.rate_limit", return_value=None):
            deleted = delete_live_message_impl(message_id=reply_id)
        self.assertTrue(deleted.get("ok"), deleted)
        self.assertEqual(frappe.db.get_value("AOS Live Message", root_id, "status"), "active")
        self.assertEqual(frappe.db.get_value("AOS Live Message", reply_id, "status"), "deleted")
        self.assertEqual(int(frappe.db.get_value("AOS Live Message", root_id, "reply_count") or 0), 0)


if __name__ == "__main__":
    unittest.main()
