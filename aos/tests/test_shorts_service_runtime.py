from __future__ import annotations

import hashlib
import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.services.shorts import service
from aos.services.shorts.identity import short_view_identity_key
from aos.services.shorts.hot_metrics import (
    _DIRTY_KEY,
    _dirty_members,
    flush_hot_metrics,
    hot_key,
    record_signal,
)
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestShortsServiceRuntime(AOSFeatureTestMixin, FrappeTestCase):
    """Focused runtime regressions for Shorts state, idempotency, and hot state."""

    def setUp(self):
        self.prefix = f"shorts-runtime-{uuid.uuid4().hex[:10]}"
        self.created_users: list[str] = []
        self.created_short_names: list[str] = []
        self.created_redis_keys: list[str] = []
        frappe.set_user("Administrator")

    def tearDown(self):
        for short_id in self.created_short_names:
            self._clear_short_ephemeral_state(short_id)
        for key in self.created_redis_keys:
            try:
                frappe.cache().delete_value(key)
            except Exception:
                pass
        frappe.set_user("Administrator")
        self.restore_localization_test_state()
        super().tearDown()

    def _published_short(self, label: str = "creator"):
        user = self.make_user(label)
        frappe.set_user(user)
        short = self.make_short(owner=user)
        short.reload()
        self.assertEqual(short.owner, user)
        return user, short

    def test_published_content_edit_requires_fresh_moderation(self):
        user, short = self._published_short()
        prior_generation = int(short.moderation_generation or 0)
        prior_revision = int(short.revision or 0)

        with patch("aos.services.shorts.service._rate"), patch("aos.services.shorts.service.classify_short"):
            response = service.update_short(
                short_id=short.name,
                version=str(short.modified),
                caption="Updated content requiring a new moderation decision",
            )

        self.assertTrue(response.get("ok"), response)
        short.reload()
        self.assertEqual(short.lifecycle_status, "Draft")
        self.assertEqual(short.moderation_status, "Draft")
        self.assertEqual(int(short.moderation_generation or 0), prior_generation + 1)
        self.assertEqual(int(short.revision or 0), prior_revision + 1)
        self.assertFalse(short.moderation_decided_by)
        self.assertFalse(short.moderation_decided_at)

    def test_published_permission_edit_does_not_force_remoderation(self):
        user, short = self._published_short()
        prior_generation = int(short.moderation_generation or 0)

        with patch("aos.services.shorts.service._rate"), patch("aos.services.shorts.service.classify_short"):
            response = service.update_short(
                short_id=short.name,
                version=str(short.modified),
                allow_downloads=1,
            )

        self.assertTrue(response.get("ok"), response)
        short.reload()
        self.assertEqual(short.lifecycle_status, "Published")
        self.assertEqual(short.moderation_status, "Approved")
        self.assertEqual(int(short.moderation_generation or 0), prior_generation)
        self.assertEqual(int(short.allow_downloads or 0), 1)

    def test_comment_replies_have_a_cursor_read_path(self):
        _user, short = self._published_short()
        with patch("aos.services.shorts.service._rate"):
            root = service.create_comment(short_id=short.name, comment="Root comment")
            root_id = root["data"]["id"]
            reply = service.create_comment(
                short_id=short.name,
                parent_comment_id=root_id,
                comment="First reply",
            )
            response = service.list_comment_replies(comment_id=root_id, limit=20)

        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response["data"]["root_comment_id"], root_id)
        self.assertEqual([item["id"] for item in response["data"]["items"]], [reply["data"]["id"]])
        self.assertIsNone(response["data"]["next_cursor"])

    def test_retry_safe_feedback_and_share_do_not_double_count(self):
        user, short = self._published_short()
        with patch("aos.services.shorts.service._rate"):
            first_feedback = service.not_interested(short_id=short.name)
            second_feedback = service.not_interested(short_id=short.name)
            first_share = service.record_share(short_id=short.name, event_id="retry-key", channel="copy_link")
            second_share = service.record_share(short_id=short.name, event_id="retry-key", channel="copy_link")

        self.assertTrue(first_feedback.get("ok") and second_feedback.get("ok"))
        self.assertTrue(first_share.get("ok") and second_share.get("ok"))
        self.assertEqual(
            frappe.db.count(
                "AOS Short Feedback",
                {"short": short.name, "user": user, "feedback_type": "not_interested"},
            ),
            1,
        )
        self.assertEqual(
            frappe.db.count(
                "AOS Short Event",
                {"short": short.name, "user": user, "event_type": "share"},
            ),
            1,
        )
        short.reload()
        self.assertEqual(int(short.share_count or 0), 1)

    def test_recommendation_event_retry_uses_atomic_dedupe(self):
        user, short = self._published_short()
        event = {
            "short_id": short.name,
            "type": "qualified_view",
            "event_id": "qualified-view-retry",
            "watch_ms": 2500,
        }
        dedupe = hashlib.sha256(
            f"{short.name}|{user}|qualified_view|qualified-view-retry".encode()
        ).hexdigest()
        self.created_redis_keys.append(f"aos:shorts:event:{dedupe}")
        with patch("aos.services.shorts.service._rate"):
            first = service.record_events(events=[event], session_id="")
            second = service.record_events(events=[event], session_id="")

        self.assertEqual(first["data"]["accepted"], 1)
        self.assertEqual(second["data"]["accepted"], 0)
        self.assertEqual(
            frappe.db.count(
                "AOS Short Event",
                {"short": short.name, "user": user, "event_type": "qualified_view"},
            ),
            1,
        )


    def test_distinct_qualified_view_events_reuse_canonical_view_identity(self):
        user, short = self._published_short()
        first_event = {
            "short_id": short.name,
            "type": "qualified_view",
            "event_id": "qualified-view-first",
            "watch_ms": 2500,
            "progress_ms": 2500,
            "source": "web_feed",
        }
        second_event = {
            **first_event,
            "event_id": "qualified-view-second",
            "watch_ms": 3200,
            "progress_ms": 3200,
        }
        for event in (first_event, second_event):
            dedupe = hashlib.sha256(
                f"{short.name}|{user}|qualified_view|{event['event_id']}".encode()
            ).hexdigest()
            self.created_redis_keys.append(f"aos:shorts:event:{dedupe}")

        with patch("aos.services.shorts.service._rate"):
            first = service.record_events(events=[first_event], session_id="")
            second = service.record_events(events=[second_event], session_id="")

        self.assertEqual(first["data"]["accepted"], 1)
        self.assertEqual(second["data"]["accepted"], 1)
        views = frappe.get_all(
            "AOS Short View",
            filters={"short": short.name, "user": user},
            fields=["name", "identity_key", "watch_ms"],
        )
        self.assertEqual(len(views), 1)
        self.assertEqual(int(views[0].watch_ms or 0), 3200)
        self.assertEqual(
            views[0].identity_key,
            short_view_identity_key(short_id=short.name, user=user),
        )

    def test_under_threshold_qualified_view_is_not_promoted(self):
        user, short = self._published_short()
        event = {
            "short_id": short.name,
            "type": "qualified_view",
            "event_id": "too-early-qualified-view",
            "watch_ms": 324,
            "progress_ms": 324,
            "source": "web_feed",
        }
        with patch("aos.services.shorts.service._rate"):
            response = service.record_events(events=[event], session_id="")

        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response["data"]["accepted"], 0)
        self.assertEqual(
            frappe.db.count(
                "AOS Short Event",
                {"short": short.name, "user": user, "event_type": "qualified_view"},
            ),
            0,
        )
        self.assertEqual(
            frappe.db.count("AOS Short View", {"short": short.name, "user": user}),
            0,
        )

    def test_hot_metric_failure_does_not_fail_durable_event_ingestion(self):
        user, short = self._published_short()
        event = {
            "short_id": short.name,
            "type": "qualified_view",
            "event_id": "durable-event-hot-cache-failure",
            "watch_ms": 2500,
            "progress_ms": 2500,
        }
        with patch("aos.services.shorts.service._rate"), patch(
            "aos.services.shorts.hot_metrics.record_signal",
            side_effect=RuntimeError("redis hot counter unavailable"),
        ):
            response = service.record_events(events=[event], session_id="")

        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response["data"]["accepted"], 1)
        self.assertEqual(
            frappe.db.count(
                "AOS Short Event",
                {"short": short.name, "user": user, "event_type": "qualified_view"},
            ),
            1,
        )

    def test_hot_metric_flush_removes_clean_dirty_membership(self):
        _user, short = self._published_short()
        self._clear_short_ephemeral_state(short.name)
        record_signal(short.name, "impression")
        cache = frappe.cache()
        dirty_before = {str(x.decode() if isinstance(x, bytes) else x) for x in (cache.smembers(_DIRTY_KEY) or set())}
        self.assertIn(short.name, dirty_before)

        # Hot hashes intentionally use raw numeric Redis values (not Frappe's
        # pickled hash helper) so HINCRBY is atomic. Verify that the write and
        # bounded scan paths see the same site-prefixed state before flushing.
        raw_hot = cache.execute_command("HGETALL", cache.make_key(hot_key(short.name))) or {}
        impression = raw_hot.get(b"impression_count", raw_hot.get("impression_count", 0))
        self.assertGreaterEqual(int(impression or 0), 1)
        selected = {str(x.decode() if isinstance(x, bytes) else x) for x in _dirty_members(cache, 5000)}
        self.assertIn(short.name, selected)

        # Capture our module-local registration seam rather than patching
        # Frappe's slotted CallbackManager. The flush still uses the real DB
        # and Redis state; only callback registration is intercepted.
        callbacks = []
        with patch(
            "aos.services.shorts.hot_metrics._register_after_commit",
            side_effect=callbacks.append,
        ) as register_callback:
            flushed = flush_hot_metrics(limit=5000)

        self.assertGreaterEqual(flushed, 1)
        register_callback.assert_called_once()
        dirty_before_commit = {str(x.decode() if isinstance(x, bytes) else x) for x in (frappe.cache().smembers(_DIRTY_KEY) or set())}
        self.assertIn(short.name, dirty_before_commit)
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()

        dirty_after = {str(x.decode() if isinstance(x, bytes) else x) for x in (frappe.cache().smembers(_DIRTY_KEY) or set())}
        self.assertNotIn(short.name, dirty_after)
        short.reload()
        self.assertGreaterEqual(int(short.impression_count or 0), 1)
