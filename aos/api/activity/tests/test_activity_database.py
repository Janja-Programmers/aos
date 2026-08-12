from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.activity.activity import clear_activity_impl, hide_activity_impl, list_activity_impl
from aos.services.accounts.identity import ensure_public_account_id
from aos.services.account_deletion_service import _cleanup_activity_account_data
from aos.services.activity_service import ActivityService
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestActivityDatabase(AOSFeatureTestMixin, FrappeTestCase):
    """DB-backed Activity Center behavior on a migrated Frappe site."""

    def setUp(self):
        self.prefix = self.make_prefix("activity")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.user = self.make_user("owner")
        self.other = self.make_user("other")
        self.target = self.make_user("target")
        self.target_public = ensure_public_account_id(self.target)
        frappe.set_user(self.user)

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _record(self, *, user=None, activity_type="user_search", group="Search", unique_key=None, **overrides):
        payload = {
            "user": user or self.user,
            "activity_group": group,
            "activity_type": activity_type,
            "target_title": "Feature activity",
            "target_subtitle": "History",
            "route_type": "user_search",
            "route_id": "feature activity",
            "metadata": {"query": "feature activity", "result_count": 2},
            "unique_key": unique_key or f"{activity_type}:{self.prefix}:{user or self.user}",
        }
        payload.update(overrides)
        return ActivityService.record_activity(**payload)

    def _list(self, **kwargs):
        with patch("aos.api.activity.activity.rate_limit", return_value=None):
            return list_activity_impl(**kwargs)

    def _hide(self, **kwargs):
        with patch("aos.api.activity.activity.rate_limit", return_value=None):
            return hide_activity_impl(**kwargs)

    def _clear(self, **kwargs):
        with patch("aos.api.activity.activity.rate_limit", return_value=None):
            return clear_activity_impl(**kwargs)

    def test_activity_indexes_exist_after_migrate(self):
        from aos.patches.v1_0.install_activity_indexes import INDEX_DEFINITIONS

        for index_name, columns, unique in INDEX_DEFINITIONS:
            rows = frappe.db.sql(
                """SELECT COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='tabAOS User Activity'
                     AND INDEX_NAME=%s ORDER BY SEQ_IN_INDEX""",
                (index_name,),
                as_dict=True,
            )
            self.assertTrue(rows, f"missing Activity index {index_name}")
            self.assertEqual(tuple(row.COLUMN_NAME for row in rows), tuple(columns), index_name)
            self.assertEqual(bool(rows[0].NON_UNIQUE), not unique, index_name)

    def test_auth_is_required_for_all_public_activity_operations(self):
        frappe.set_user("Guest")
        self.assertFalse(self._list().get("ok"))
        self.assertFalse(self._hide(activity_id="missing").get("ok"))
        self.assertFalse(self._clear().get("ok"))

    def test_list_is_private_strict_and_bounded(self):
        own = self._record()
        self._record(user=self.other, unique_key=f"other:{self.prefix}")
        valid = self._list(limit=10, start=0)
        unknown = self._list(unexpected="x")
        invalid_limit = self._list(limit="not-a-number")
        conflicting_group = self._list(group="Search", activity_group="Ads")

        self.assertTrue(valid.get("ok"), valid)
        ids = {row["id"] for row in valid["data"]["items"]}
        self.assertIn(own, ids)
        self.assertEqual(len(ids), 1)
        for response in (unknown, invalid_limit, conflicting_group):
            self.assertFalse(response.get("ok"), response)
            self.assertEqual(response.get("error"), "VALIDATION_ERROR")

    def test_public_serializer_redacts_internal_user_doctype_and_private_metadata(self):
        activity_id = ActivityService.record_activity(
            user=self.user,
            activity_group="Social",
            activity_type="user_follow",
            target_doctype="User",
            target_name=self.target,
            target_title="Feature Target",
            target_subtitle="Profile",
            route_type="profile",
            route_id=self.target_public,
            metadata={
                "target_user": self.target_public,
                "target_display_name": "Feature Target",
                "seller_user": self.target,
                "session_id": "private-session",
                "report_id": "REPORT-INTERNAL",
            },
            unique_key=f"user-follow:{self.prefix}",
        )
        response = self._list(group="Social", type="user_follow")
        self.assertTrue(response.get("ok"), response)
        item = next(row for row in response["data"]["items"] if row["id"] == activity_id)
        self.assertEqual(item["target"]["doctype"], "profile")
        self.assertEqual(item["target"]["name"], self.target_public)
        self.assertNotIn(self.target, repr(item))
        self.assertNotIn("seller_user", item["metadata"])
        self.assertNotIn("session_id", item["metadata"])
        self.assertNotIn("report_id", item["metadata"])

    def test_repeatable_activity_coalesces_and_count_is_server_owned(self):
        key = f"watch:{self.prefix}"
        first = ActivityService.record_or_update_activity(
            user=self.user,
            activity_group="Shorts",
            activity_type="short_watch",
            target_doctype="AOS Short",
            target_name="SHORT-TEST",
            target_title="Short",
            route_type="short",
            route_id="SHORT-TEST",
            metadata={"watch_ms": 100},
            unique_key=key,
        )
        second = ActivityService.record_or_update_activity(
            user=self.user,
            activity_group="Shorts",
            activity_type="short_watch",
            target_doctype="AOS Short",
            target_name="SHORT-TEST",
            target_title="Short",
            route_type="short",
            route_id="SHORT-TEST",
            metadata={"watch_ms": 200},
            unique_key=key,
        )
        self.assertEqual(first, second)
        row = frappe.db.get_value(
            "AOS User Activity", first, ["count", "active_key", "metadata_json"], as_dict=True
        )
        self.assertEqual(int(row.count or 0), 2)
        self.assertTrue(row.active_key)
        self.assertEqual(
            frappe.db.count("AOS User Activity", {"user": self.user, "status": "Active", "unique_key": key}),
            1,
        )

    def test_hide_is_idempotent_and_idor_safe(self):
        activity_id = self._record()
        frappe.set_user(self.other)
        cross_user = self._hide(activity_id=activity_id)
        self.assertFalse(cross_user.get("ok"), cross_user)
        self.assertEqual(cross_user.get("error"), "NOT_FOUND")
        self.assertEqual(frappe.db.get_value("AOS User Activity", activity_id, "status"), "Active")

        frappe.set_user(self.user)
        first = self._hide(activity_id=activity_id)
        repeated = self._hide(id=activity_id)
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(repeated.get("ok"), repeated)
        row = frappe.db.get_value("AOS User Activity", activity_id, ["status", "active_key"], as_dict=True)
        self.assertEqual(row.status, "Hidden")
        self.assertFalse(row.active_key)
        listed = self._list()
        self.assertNotIn(activity_id, {item["id"] for item in listed["data"]["items"]})

    def test_new_occurrence_after_hide_creates_fresh_active_row(self):
        key = f"search:{self.prefix}"
        first = ActivityService.record_or_update_activity(
            user=self.user,
            activity_group="Search",
            activity_type="user_search",
            target_title="alpha",
            route_type="user_search",
            route_id="alpha",
            metadata={"query": "alpha", "result_count": 1},
            unique_key=key,
        )
        self.assertTrue(self._hide(activity_id=first).get("ok"))
        second = ActivityService.record_or_update_activity(
            user=self.user,
            activity_group="Search",
            activity_type="user_search",
            target_title="alpha",
            route_type="user_search",
            route_id="alpha",
            metadata={"query": "alpha", "result_count": 2},
            unique_key=key,
        )
        self.assertNotEqual(first, second)
        self.assertEqual(frappe.db.count("AOS User Activity", {"user": self.user, "unique_key": key}), 2)
        self.assertEqual(frappe.db.count("AOS User Activity", {"user": self.user, "unique_key": key, "status": "Active"}), 1)

    def test_clear_is_scoped_and_keeps_other_groups(self):
        ads_id = self._record(activity_type="ad_view", group="Ads", unique_key=f"ad:{self.prefix}", route_type="ad", route_id="AD-1")
        shorts_id = self._record(activity_type="short_watch", group="Shorts", unique_key=f"short:{self.prefix}", route_type="short", route_id="SHORT-1")
        response = self._clear(group="Ads")
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response["data"]["cleared_count"], 1)
        self.assertEqual(frappe.db.get_value("AOS User Activity", ads_id, "status"), "Cleared")
        self.assertEqual(frappe.db.get_value("AOS User Activity", shorts_id, "status"), "Active")

    def test_legacy_identity_metadata_is_serialized_as_opaque_public_ids(self):
        activity_id = ActivityService.record_activity(
            user=self.user,
            activity_group="Shorts",
            activity_type="short_watch",
            target_doctype="AOS Short",
            target_name="SHORT-LEGACY",
            target_title="Legacy short",
            route_type="short",
            route_id="SHORT-LEGACY",
            metadata={"short_owner": self.target, "seller": self.target, "watch_ms": 10},
            unique_key=f"legacy-identities:{self.prefix}",
        )
        response = self._list(group="Shorts", type="short_watch")
        self.assertTrue(response.get("ok"), response)
        item = next(row for row in response["data"]["items"] if row["id"] == activity_id)
        self.assertEqual(item["metadata"]["short_owner"], self.target_public)
        self.assertTrue(str(item["metadata"]["seller"]).startswith("SELLER-"))
        self.assertNotIn(self.target, repr(item))

    def test_account_deletion_redacts_other_users_removed_content_snapshots(self):
        ad = self.make_ad(seller_user=self.user)
        short = self.make_short(owner=self.user)
        live = self.make_live(host=self.user)
        frappe.set_user(self.other)
        rows = [
            ActivityService.record_activity(
                user=self.other,
                activity_group="Ads",
                activity_type="ad_view",
                target_doctype="AOS Ad",
                target_name=ad.name,
                target_title="Private ad snapshot",
                route_type="ad",
                route_id=ad.name,
                unique_key=f"deleted-ad:{self.prefix}",
            ),
            ActivityService.record_activity(
                user=self.other,
                activity_group="Shorts",
                activity_type="short_watch",
                target_doctype="AOS Short",
                target_name=short.name,
                target_title="Private short snapshot",
                route_type="short",
                route_id=short.name,
                unique_key=f"deleted-short:{self.prefix}",
            ),
            ActivityService.record_activity(
                user=self.other,
                activity_group="Live",
                activity_type="live_join",
                target_doctype="AOS Live Stream",
                target_name=live.name,
                target_title="Private live snapshot",
                route_type="live",
                route_id=live.name,
                unique_key=f"deleted-live:{self.prefix}",
            ),
        ]
        frappe.set_user("Administrator")
        summary = _cleanup_activity_account_data(user=self.user, sellers=[ad.seller])
        self.assertGreaterEqual(summary["activity_content_rows_redacted"], 3)
        for activity_id in rows:
            row = frappe.db.get_value(
                "AOS User Activity",
                activity_id,
                ["status", "target_title", "target_image", "active_key"],
                as_dict=True,
            )
            self.assertEqual(row.status, "Hidden")
            self.assertEqual(row.target_title, "Unavailable content")
            self.assertFalse(row.target_image)
            self.assertFalse(row.active_key)

    def test_account_deletion_removes_private_history_and_redacts_other_profile_snapshots(self):
        own = self._record()
        frappe.set_user(self.other)
        other_row = ActivityService.record_activity(
            user=self.other,
            activity_group="Social",
            activity_type="user_follow",
            target_doctype="User",
            target_name=self.user,
            target_title="Feature Owner",
            target_subtitle="Profile",
            target_image="https://example.invalid/avatar.jpg",
            route_type="profile",
            route_id=ensure_public_account_id(self.user),
            metadata={"target_user": ensure_public_account_id(self.user), "target_display_name": "Feature Owner"},
            unique_key=f"follow-deleted:{self.prefix}",
        )
        frappe.set_user("Administrator")
        summary = _cleanup_activity_account_data(user=self.user)
        self.assertGreaterEqual(summary["activity_rows_removed"], 1)
        self.assertFalse(frappe.db.exists("AOS User Activity", own))
        redacted = frappe.db.get_value(
            "AOS User Activity",
            other_row,
            ["status", "target_title", "target_image", "metadata_json", "active_key"],
            as_dict=True,
        )
        self.assertEqual(redacted.status, "Hidden")
        self.assertEqual(redacted.target_title, "Deleted account")
        self.assertFalse(redacted.target_image)
        self.assertFalse(redacted.active_key)
        if isinstance(redacted.metadata_json, str):
            self.assertEqual(redacted.metadata_json.strip(), "{}")
        else:
            self.assertEqual(redacted.metadata_json or {}, {})


if __name__ == "__main__":
    import unittest

    unittest.main()
