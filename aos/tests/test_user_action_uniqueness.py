from __future__ import annotations

import uuid

import frappe
from frappe.exceptions import TimestampMismatchError
from frappe.tests.utils import FrappeTestCase

from aos.api.live.tracking import track_join_impl
from aos.api.notifications.token import register_push_token_impl
from aos.api.shorts.tracking import _update_short_view_watch_progress, track_view_impl
from aos.api.social.block import block_user_impl
from aos.patches.v1_0.add_unique_constraints import (
    USER_ACTION_UNIQUE_CONSTRAINTS,
    execute as apply_unique_constraints,
)
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestUserActionUniqueness(AOSFeatureTestMixin, FrappeTestCase):
    """Tests for race-sensitive user-action duplicate protection."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._ensure_unique_constraints()

    @classmethod
    def _ensure_unique_constraints(cls):
        """Apply the merged uniqueness patch when a dev site already logged the old patch.

        Fresh installs get these indexes through patches.txt during migrate. Existing
        staging/dev sites may already have the generic patch in Patch Log from before
        this migration was expanded, so this keeps the test suite deterministic without
        introducing milestone-specific patch filenames.
        """
        missing = [
            index
            for index in USER_ACTION_UNIQUE_CONSTRAINTS
            if not cls._unique_index_exists_static(
                doctype=index["doctype"],
                constraint_name=index["constraint_name"],
            )
        ]

        if missing:
            apply_unique_constraints()
            frappe.db.commit()

    def setUp(self):
        self.prefix = f"unique-{uuid.uuid4().hex[:10]}"
        self.created_users: list[str] = []
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")
        self._delete_test_rows()
        self.restore_localization_test_state()
        frappe.db.commit()

    def test_unique_indexes_exist(self):
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
        blocker = self._make_user("blocker")
        blocked = self._make_user("blocked")
        frappe.set_user(blocker)

        first = block_user_impl(target_user=blocked, reason="spam")
        second = block_user_impl(target_user=blocked, reason="spam again")

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(
            frappe.db.count(
                "AOS User Block",
                {
                    "blocker_user": blocker,
                    "blocked_user": blocked,
                    "status": "Active",
                },
            ),
            1,
        )

    def test_live_track_join_double_request_keeps_one_active_view(self):
        host = self._make_user("host")
        viewer = self._make_user("viewer")
        live = self._make_live(host)
        session_id = f"{self.prefix}-live-session"
        frappe.set_user(viewer)

        first = track_join_impl(live_id=live.name, session_id=session_id)
        second = track_join_impl(live_id=live.name, session_id=session_id)

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(
            frappe.db.count(
                "AOS Live Stream View",
                {
                    "live_stream": live.name,
                    "session_id": session_id,
                    "is_active": 1,
                },
            ),
            1,
        )

    def test_short_track_view_double_request_keeps_one_daily_view(self):
        user = self._make_user("viewer")
        short = self._make_short(user)
        session_id = f"{self.prefix}-short-session"
        frappe.set_user(user)

        from unittest.mock import patch

        with (
            patch("aos.api.shorts.tracking.emit_analytics_event"),
            patch("aos.api.shorts.tracking.frappe.enqueue"),
        ):
            first = track_view_impl(short_id=short.name, watch_ms=1000, session_id=session_id)
            second = track_view_impl(short_id=short.name, watch_ms=2500, session_id=session_id)

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(
            frappe.db.count(
                "AOS Short View",
                {
                    "short": short.name,
                    "identity_key": f"user:{user}",
                },
            ),
            1,
        )
        self.assertEqual(
            int(
                frappe.db.get_value(
                    "AOS Short View",
                    {"short": short.name, "identity_key": f"user:{user}"},
                    "watch_ms",
                )
                or 0
            ),
            2500,
        )

    def test_short_view_progress_retries_timestamp_mismatch_and_keeps_max_watch(self):
        class ConcurrentView:
            def __init__(self):
                self.watch_ms = 1000
                self.last_seen_at = None
                self.save_calls = 0
                self.reload_calls = 0

            def save(self, *, ignore_permissions=False):
                self.save_calls += 1
                if self.save_calls == 1:
                    raise TimestampMismatchError("simulated concurrent progress update")

            def reload(self):
                self.reload_calls += 1
                # Another request committed more progress before this retry.
                self.watch_ms = 2000

        doc = ConcurrentView()
        changed = _update_short_view_watch_progress(doc, 2500)

        self.assertTrue(changed)
        self.assertEqual(doc.watch_ms, 2500)
        self.assertEqual(doc.save_calls, 2)
        self.assertEqual(doc.reload_calls, 1)

    def test_short_track_view_survives_ranking_enqueue_failure(self):
        user = self._make_user("ranking-queue")
        short = self._make_short(user)
        session_id = f"{self.prefix}-ranking-session"
        frappe.set_user(user)

        from unittest.mock import patch

        with (
            patch("aos.api.shorts.tracking.emit_analytics_event"),
            patch(
                "aos.api.shorts.tracking.frappe.enqueue",
                side_effect=RuntimeError("simulated Redis/worker outage"),
            ),
            patch("aos.api.shorts.tracking.frappe.log_error") as log_error,
        ):
            result = track_view_impl(
                short_id=short.name,
                watch_ms=2500,
                session_id=session_id,
            )

        self.assertTrue(result.get("ok"), result)
        self.assertEqual(
            frappe.db.count(
                "AOS Short View",
                {
                    "short": short.name,
                    "identity_key": f"user:{user}",
                },
            ),
            1,
        )
        log_error.assert_called()

    def test_push_token_double_registration_keeps_one_active_device_token(self):
        user = self._make_user("push")
        frappe.set_user(user)
        device_id = f"{self.prefix}-device"

        first = register_push_token_impl(
            token=f"{self.prefix}-token-1",
            device_type="android",
            device_id=device_id,
        )
        second = register_push_token_impl(
            token=f"{self.prefix}-token-2",
            device_type="android",
            device_id=device_id,
        )

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(
            frappe.db.count(
                "AOS Push Token",
                {
                    "user": user,
                    "device_id": device_id,
                    "is_active": 1,
                },
            ),
            1,
        )

    def test_comment_like_unique_constraint_blocks_duplicate_rows(self):
        user = self._make_user("commenter")
        short = self._make_short(user)
        comment = self._make_comment(short.name, user)
        frappe.set_user(user)

        frappe.get_doc(
            {
                "doctype": "AOS Short Comment Like",
                "comment": comment.name,
                "user": user,
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()

        with self.assertRaises(Exception):
            frappe.get_doc(
                {
                    "doctype": "AOS Short Comment Like",
                    "comment": comment.name,
                    "user": user,
                }
            ).insert(ignore_permissions=True)

        frappe.db.rollback()
        self.assertEqual(
            frappe.db.count(
                "AOS Short Comment Like",
                {
                    "comment": comment.name,
                    "user": user,
                },
            ),
            1,
        )

    # FIXTURE HELPERS
    def _make_user(self, label: str) -> str:
        email = f"{self.prefix}-{label}@example.com"
        if not frappe.db.exists("User", email):
            user = frappe.get_doc(
                {
                    "doctype": "User",
                    "email": email,
                    "first_name": "Unique",
                    "last_name": label.title(),
                    "enabled": 1,
                    "send_welcome_email": 0,
                }
            )
            user.insert(ignore_permissions=True)

        if not frappe.db.exists("AOS Profile", email):
            frappe.get_doc(
                {
                    "doctype": "AOS Profile",
                    "user": email,
                    "account_status": "Active",
                    "is_deleted": 0,
                }
            ).insert(ignore_permissions=True)

        self._ensure_user_preference(email)

        self.created_users.append(email)
        frappe.db.commit()
        return email

    def _make_live(self, host: str):
        live = frappe.get_doc(
            {
                "doctype": "AOS Live Stream",
                "title": f"{self.prefix} live",
                "host_user": host,
                "status": "live",
                "is_active": 1,
            }
        )
        live.insert(ignore_permissions=True)
        frappe.db.commit()
        return live

    def _make_short(self, owner: str):
        media = frappe.get_doc(
            {
                "doctype": "AOS Media Object",
                "owner_user": owner,
                "purpose": "short_video_raw",
                "status": "Uploaded",
                "visibility": "Private",
                "bucket": "aos-test",
                "object_key": f"tests/{self.prefix}/{uuid.uuid4().hex}.mp4",
                "original_filename": "short.mp4",
                "content_type": "video/mp4",
                "size_bytes": 1024,
            }
        )
        media.insert(ignore_permissions=True)

        short = frappe.get_doc(
            {
                "doctype": "AOS Short",
                "file_key": f"tests/{self.prefix}/{uuid.uuid4().hex}.mp4",
                "raw_video_media": media.name,
                "status": "ready",
                "visibility_status": "visible",
                "content_mode": "vibes",
                "audience": "everyone",
                "allow_comments": 1,
                "allow_downloads": 0,
                "duration_seconds": 10,
            }
        )
        short.insert(ignore_permissions=True)
        frappe.db.commit()
        return short

    def _make_comment(self, short_id: str, user: str):
        frappe.set_user(user)
        comment = frappe.get_doc(
            {
                "doctype": "AOS Short Comment",
                "short": short_id,
                "user": user,
                "comment": "Duplicate protection test comment",
                "status": "active",
            }
        )
        comment.insert(ignore_permissions=True)
        frappe.db.commit()
        return comment

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

    def _ensure_user_preference(self, user: str):
        if frappe.db.exists("AOS User Preference", {"user": user}):
            return

        country, language, currency = self._preference_defaults()

        frappe.get_doc(
            {
                "doctype": "AOS User Preference",
                "user": user,
                "country": country,
                "language": language,
                "currency": currency,
            }
        ).insert(ignore_permissions=True)

    def _preference_defaults(self) -> tuple[str, str, str]:
        return self.preference_defaults()

    def _delete_test_rows(self):
        like = f"{self.prefix}%"
        email_like = f"{self.prefix}-%@example.com"

        # Delete feature rows before users/profiles to satisfy link constraints.
        frappe.db.sql("DELETE FROM `tabAOS Short Comment Like` WHERE user LIKE %s", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS Short Comment` WHERE user LIKE %s", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS Short View` WHERE user LIKE %s OR session_id LIKE %s", (email_like, like))
        frappe.db.sql("DELETE FROM `tabAOS Live Stream View` WHERE user LIKE %s OR session_id LIKE %s", (email_like, like))
        frappe.db.sql("DELETE FROM `tabAOS User Block` WHERE blocker_user LIKE %s OR blocked_user LIKE %s", (email_like, email_like))
        frappe.db.sql("DELETE FROM `tabAOS Push Token` WHERE user LIKE %s OR device_id LIKE %s", (email_like, like))
        frappe.db.sql("DELETE FROM `tabAOS User Activity` WHERE user LIKE %s", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS Live Stream` WHERE title LIKE %s", (like,))
        frappe.db.sql("DELETE FROM `tabAOS Short` WHERE file_key LIKE %s", (f"tests/{self.prefix}/%",))
        frappe.db.sql("DELETE FROM `tabAOS Media Object` WHERE owner_user LIKE %s", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS User Preference` WHERE user LIKE %s", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS Profile` WHERE user LIKE %s", (email_like,))

        for user in self.created_users:
            if frappe.db.exists("User", user):
                frappe.delete_doc("User", user, ignore_permissions=True, force=True)
