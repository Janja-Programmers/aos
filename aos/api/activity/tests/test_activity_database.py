from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from aos.api.activity.activity import clear_activity_impl, hide_activity_impl, list_activity_impl
from aos.patches.v1_0 import install_activity_indexes
from aos.services.account_purge_service import _purge_activity_private_batch
from aos.services.accounts.identity import ensure_public_account_id
from aos.services.activity.identity import normalize_activity_id
from aos.services.activity_service import ActivityService
from aos.services.social.service import SocialService
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestActivityDatabase(AOSFeatureTestMixin, FrappeTestCase):
    """Current Activity behavior on a migrated Frappe site."""

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

    def _row(self, public_id: str, fields):
        return frappe.db.get_value(
            "AOS User Activity",
            {"public_id": public_id},
            fields,
            as_dict=True,
        )

    def _search_event(self, suffix: str, *, user: str | None = None, occurred_at=None) -> str:
        query = f"{self.prefix} {suffix}"
        return ActivityService.record_or_update_activity(
            user=user or self.user,
            activity_group="Search",
            activity_type="user_search",
            target_title=query,
            target_subtitle="User search",
            route_type="user_search",
            route_id=query,
            metadata={"query": query, "result_count": 1},
            occurred_at=occurred_at,
            unique_key=f"search:{self.prefix}:{suffix}",
        )

    def _profile_once(self, suffix: str = "report", *, user: str | None = None) -> str:
        return ActivityService.record_activity(
            user=user or self.user,
            activity_group="Social",
            activity_type="user_report",
            target_doctype="User",
            target_name=self.target,
            target_title="Feature Target",
            target_subtitle="Reported user",
            route_type="profile",
            route_id=self.target_public,
            metadata={"target_user": self.target_public, "reason": "Spam"},
            unique_key=f"user-report:{self.prefix}:{suffix}",
        )

    def _list(self, **kwargs):
        with patch("aos.api.activity.activity.rate_limit", return_value=None):
            return list_activity_impl(**kwargs)

    def _hide(self, **kwargs):
        with patch("aos.api.activity.activity.rate_limit", return_value=None):
            return hide_activity_impl(**kwargs)

    def _clear(self, **kwargs):
        with patch("aos.api.activity.activity.rate_limit", return_value=None):
            return clear_activity_impl(**kwargs)

    def test_indexes_and_public_identity_are_current(self):
        frappe.set_user("Administrator")
        install_activity_indexes.execute()
        rows = frappe.db.sql(
            """
            SELECT INDEX_NAME, NON_UNIQUE
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='tabAOS User Activity'
            """,
            as_dict=True,
        )
        by_name = {str(row.INDEX_NAME): int(row.NON_UNIQUE) for row in rows}
        self.assertEqual(by_name.get("uq_aos_activity_active"), 0)
        self.assertEqual(by_name.get("uq_aos_activity_event"), 0)
        for name in (
            "idx_aos_activity_user_timeline",
            "idx_aos_activity_group_timeline",
            "idx_aos_activity_type_timeline",
            "idx_aos_activity_route_target",
            "idx_aos_activity_retention",
        ):
            self.assertIn(name, by_name)
        frappe.set_user(self.user)
        activity_id = self._search_event("identity")
        self.assertTrue(normalize_activity_id(activity_id))
        row = self._row(activity_id, ["name", "public_id"])
        self.assertEqual(row.public_id, activity_id)
        self.assertNotEqual(row.name, activity_id)

    def test_canonical_creation_rejects_wrong_mode_schema_and_metadata(self):
        activity_id = self._search_event("valid")
        self.assertTrue(activity_id)
        with self.assertRaises(ValueError):
            ActivityService.record_activity(
                user=self.user,
                activity_group="Search",
                activity_type="user_search",
                route_type="user_search",
                route_id="invalid-mode",
                metadata={"query": "invalid-mode"},
                unique_key=f"bad-mode:{self.prefix}",
            )
        with self.assertRaises(ValueError):
            ActivityService.record_or_update_activity(
                user=self.user,
                activity_group="Ads",
                activity_type="user_search",
                route_type="user_search",
                route_id="bad-group",
                metadata={"query": "bad-group"},
                unique_key=f"bad-group:{self.prefix}",
            )
        with self.assertRaises(ValueError):
            ActivityService.record_or_update_activity(
                user=self.user,
                activity_group="Search",
                activity_type="user_search",
                route_type="user_search",
                route_id="bad-metadata",
                metadata={"query": "x", "forged": "value"},
                unique_key=f"bad-metadata:{self.prefix}",
            )
        with self.assertRaises(ValueError):
            ActivityService.record_or_update_activity(
                user=self.user, activity_group="Search", activity_type="user_search",
                route_type="user_search", route_id="bad-type",
                metadata={"query": "x", "result_count": "1"},
                unique_key=f"bad-type:{self.prefix}",
            )
        with self.assertRaises(ValueError):
            ActivityService.record_or_update_activity(
                user=self.user, activity_group="Social", activity_type="user_follow",
                target_doctype="User", target_name=self.target, route_type="profile",
                route_id=self.target_public, metadata={},
                unique_key=f"missing-required:{self.prefix}",
            )

    def test_coalesced_retry_updates_one_active_row(self):
        key = f"search:{self.prefix}:coalesce"
        first = self._search_event("coalesce")
        second = ActivityService.record_or_update_activity(
            user=self.user,
            activity_group="Search",
            activity_type="user_search",
            target_title="updated",
            route_type="user_search",
            route_id=f"{self.prefix} coalesce",
            metadata={"query": f"{self.prefix} coalesce", "result_count": 2},
            unique_key=key,
        )
        self.assertEqual(first, second)
        row = self._row(first, ["count", "active_key", "event_key"])
        self.assertEqual(int(row.count or 0), 2)
        self.assertTrue(row.active_key)
        self.assertFalse(row.event_key)
        self.assertEqual(
            frappe.db.count("AOS User Activity", {"user": self.user, "unique_key": ActivityService.normalize_unique_key(key), "status": "Active"}),
            1,
        )

    def test_one_off_retry_survives_hide_without_duplicate(self):
        first = self._profile_once("idempotent")
        second = self._profile_once("idempotent")
        self.assertEqual(first, second)
        row = self._row(first, ["event_key", "active_key", "status"])
        self.assertTrue(row.event_key)
        self.assertFalse(row.active_key)
        self.assertEqual(row.status, "Active")
        self.assertTrue(self._hide(activity_id=first).get("ok"))
        third = self._profile_once("idempotent")
        self.assertEqual(first, third)
        self.assertEqual(self._row(first, ["status"]).status, "Hidden")
        self.assertEqual(
            frappe.db.count("AOS User Activity", {"user": self.user, "unique_key": ActivityService.normalize_unique_key(f"user-report:{self.prefix}:idempotent")}),
            1,
        )

    def test_concurrent_duplicate_event_retry_reuses_database_winner(self):
        first = self._profile_once("concurrent-race")
        original_find = ActivityService._find_by_event_key
        calls = 0

        def stale_then_database(event_key):
            nonlocal calls
            calls += 1
            if calls == 1:
                # Simulate the producer's optimistic pre-check losing a race:
                # another worker has already committed the same logical event.
                return None
            return original_find(event_key)

        with patch.object(ActivityService, "_find_by_event_key", side_effect=stale_then_database):
            retry = self._profile_once("concurrent-race")

        self.assertEqual(retry, first)
        self.assertGreaterEqual(calls, 2)
        row = self._row(first, ["event_key"])
        self.assertEqual(frappe.db.count("AOS User Activity", {"event_key": row.event_key}), 1)

    def test_database_unique_event_key_is_final_concurrency_boundary(self):
        first = self._profile_once("db-unique")
        row = self._row(first, ["unique_key", "event_key"])
        duplicate = frappe.get_doc(
            {
                "doctype": "AOS User Activity",
                "user": self.user,
                "activity_group": "Social",
                "activity_type": "user_report",
                "status": "Active",
                "target_doctype": "User",
                "target_name": self.target,
                "target_title": "Duplicate",
                "route_type": "profile",
                "route_id": self.target_public,
                "metadata_json": {"target_user": self.target_public},
                "unique_key": row.unique_key,
            }
        )
        with self.assertRaises(Exception):
            duplicate.insert(ignore_permissions=True)
        self.assertEqual(frappe.db.count("AOS User Activity", {"event_key": row.event_key}), 1)

    def test_guest_unknown_fields_and_injection_like_filters_are_rejected(self):
        frappe.set_user("Guest")
        guest = self._list()
        self.assertFalse(guest.get("ok"), guest)
        frappe.set_user(self.user)
        for payload in (
            {"user": self.other},
            {"sort": "creation desc"},
            {"type": "user_search' OR 1=1 --"},
            {"group": "Ads", "type": "user_search"},
            {"limit": 51},
        ):
            response = self._list(**payload)
            self.assertFalse(response.get("ok"), (payload, response))
            self.assertEqual(response.get("error"), "VALIDATION_ERROR")

    def test_hide_is_public_id_owner_scoped_and_idempotent(self):
        activity_id = self._search_event("hide")
        internal = self._row(activity_id, ["name"]).name
        self.assertFalse(self._hide(activity_id=internal).get("ok"))
        frappe.set_user(self.other)
        cross_user = self._hide(activity_id=activity_id)
        self.assertFalse(cross_user.get("ok"), cross_user)
        self.assertEqual(cross_user.get("error"), "NOT_FOUND")
        frappe.set_user(self.user)
        first = self._hide(activity_id=activity_id)
        second = self._hide(activity_id=activity_id)
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        row = self._row(activity_id, ["status", "active_key"])
        self.assertEqual(row.status, "Hidden")
        self.assertFalse(row.active_key)

    def test_keyset_pagination_is_deterministic_and_cursor_scope_bound(self):
        base = now_datetime() - timedelta(minutes=10)
        created = {
            self._search_event(str(index), occurred_at=base + timedelta(seconds=index))
            for index in range(5)
        }
        first = self._list(limit=2, group="Search")
        self.assertTrue(first.get("ok"), first)
        self.assertEqual(len(first["data"]["items"]), 2)
        self.assertTrue(first["data"]["has_more"])
        cursor = first["data"]["next_cursor"]
        self.assertTrue(cursor)

        seen = {item["id"] for item in first["data"]["items"]}
        second = self._list(limit=2, group="Search", cursor=cursor)
        self.assertTrue(second.get("ok"), second)
        second_ids = {item["id"] for item in second["data"]["items"]}
        self.assertFalse(seen & second_ids)
        seen |= second_ids
        if second["data"]["next_cursor"]:
            third = self._list(limit=2, group="Search", cursor=second["data"]["next_cursor"])
            self.assertTrue(third.get("ok"), third)
            seen |= {item["id"] for item in third["data"]["items"]}
        self.assertTrue(created <= seen)

        malformed = self._list(limit=2, group="Search", cursor="not-a-cursor")
        self.assertEqual(malformed.get("error"), "VALIDATION_ERROR")
        changed_filter = self._list(limit=2, group="Search", type="user_search", cursor=cursor)
        self.assertEqual(changed_filter.get("error"), "VALIDATION_ERROR")
        frappe.set_user(self.other)
        wrong_owner = self._list(limit=2, group="Search", cursor=cursor)
        self.assertEqual(wrong_owner.get("error"), "VALIDATION_ERROR")


    def test_page_defaults_and_maximum_are_bounded(self):
        for index in range(3):
            self._search_event(f"bounds-{index}")
        default_page = self._list(group="Search")
        self.assertTrue(default_page.get("ok"), default_page)
        self.assertEqual(default_page["data"]["limit"], 20)
        maximum_page = self._list(group="Search", limit=50)
        self.assertTrue(maximum_page.get("ok"), maximum_page)
        self.assertEqual(maximum_page["data"]["limit"], 50)

    def test_clear_is_scoped_and_bounded_contract(self):
        first = self._search_event("clear-a")
        second = self._search_event("clear-b")
        social = ActivityService.record_or_update_activity(
            user=self.user,
            activity_group="Social",
            activity_type="user_follow",
            target_doctype="User",
            target_name=self.target,
            target_title="Target",
            route_type="profile",
            route_id=self.target_public,
            metadata={"target_user": self.target_public},
            unique_key=f"follow:{self.prefix}",
        )
        response = self._clear(group="Search")
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response["data"]["cleared_count"], 2)
        self.assertFalse(response["data"]["has_more"])
        self.assertEqual(self._row(first, ["status"]).status, "Cleared")
        self.assertEqual(self._row(second, ["status"]).status, "Cleared")
        self.assertEqual(self._row(social, ["status"]).status, "Active")

    def test_profile_block_and_disabled_account_fail_closed(self):
        follow = ActivityService.record_or_update_activity(
            user=self.user,
            activity_group="Social",
            activity_type="user_follow",
            target_doctype="User",
            target_name=self.target,
            target_title="Target",
            route_type="profile",
            route_id=self.target_public,
            metadata={"target_user": self.target_public},
            unique_key=f"follow-privacy:{self.prefix}",
        )
        available = self._list(group="Social", type="user_follow")
        item = next(row for row in available["data"]["items"] if row["id"] == follow)
        self.assertTrue(item["resource_available"])

        SocialService().block(actor=self.user, payload={"account_id": self.target_public})
        blocked = self._list(group="Social", type="user_follow")
        item = next(row for row in blocked["data"]["items"] if row["id"] == follow)
        self.assertFalse(item["resource_available"])
        self.assertIsNone(item["target"]["id"])
        self.assertEqual(item["metadata"], {})

        # Independent disabled-account fail-closed case.
        SocialService().unblock(actor=self.user, payload={"account_id": self.target_public})
        frappe.db.set_value("User", self.target, "enabled", 0, update_modified=False)
        disabled = self._list(group="Social", type="user_follow")
        item = next(row for row in disabled["data"]["items"] if row["id"] == follow)
        self.assertFalse(item["resource_available"])

    def test_ad_short_and_live_resource_lifecycle_fail_closed(self):
        ad = self.make_ad(seller_user=self.other)
        short = self.make_short(owner=self.other)
        live = self.make_live(host=self.other)
        frappe.db.set_value("AOS Live Stream", live.name, "is_active", 1, update_modified=False)

        ad_activity = ActivityService.record_or_update_activity(
            user=self.user,
            activity_group="Ads",
            activity_type="ad_view",
            target_doctype="AOS Ad",
            target_name=ad.name,
            target_title=ad.title,
            route_type="ad",
            route_id=ad.public_id,
            unique_key=f"ad-view:{self.prefix}",
        )
        short_activity = ActivityService.record_activity(
            user=self.user,
            activity_group="Shorts",
            activity_type="short_report",
            target_doctype="AOS Short",
            target_name=short.name,
            target_title="Short",
            route_type="short",
            route_id=short.name,
            metadata={"reason": "Spam"},
            unique_key=f"short-report:{self.prefix}",
        )
        live_activity = ActivityService.record_or_update_activity(
            user=self.user,
            activity_group="Live",
            activity_type="live_join",
            target_doctype="AOS Live Stream",
            target_name=live.name,
            target_title=live.title,
            route_type="live",
            route_id=live.name,
            metadata={"live_id": live.name, "host_user": ensure_public_account_id(self.other)},
            unique_key=f"live-join:{self.prefix}",
        )

        for activity_id, group in ((ad_activity, "Ads"), (short_activity, "Shorts"), (live_activity, "Live")):
            response = self._list(group=group)
            item = next(row for row in response["data"]["items"] if row["id"] == activity_id)
            self.assertTrue(item["resource_available"], (group, item))

        frappe.db.set_value("AOS Ad", ad.name, "status", "Deleted", update_modified=False)
        frappe.db.set_value("AOS Short", short.name, "lifecycle_status", "Deleted", update_modified=False)
        frappe.db.set_value("AOS Live Stream", live.name, {"status": "ended", "is_active": 0}, update_modified=False)
        for activity_id, group in ((ad_activity, "Ads"), (short_activity, "Shorts")):
            response = self._list(group=group)
            item = next(row for row in response["data"]["items"] if row["id"] == activity_id)
            self.assertFalse(item["resource_available"], (group, item))
            self.assertIsNone(item["target"]["id"])
            self.assertEqual(item["metadata"], {})

        # Activity follows Live's canonical get-live access contract: ending a
        # stream does not itself make its historical detail unavailable.
        response = self._list(group="Live")
        item = next(row for row in response["data"]["items"] if row["id"] == live_activity)
        self.assertTrue(item["resource_available"], item)
        self.assertEqual(item["target"]["id"], live.name)

    def test_permanent_account_purge_removes_owner_private_activity(self):
        activity_id = self._search_event("purge-owner")
        frappe.set_user("Administrator")
        summary = _purge_activity_private_batch(user=self.user, limit=10)
        self.assertGreaterEqual(summary["activity_rows_removed"], 1)
        self.assertFalse(frappe.db.exists("AOS User Activity", {"public_id": activity_id}))

    def test_retention_delete_is_bounded(self):
        first = self._search_event("expired-1")
        second = self._search_event("expired-2")
        old = now_datetime() - timedelta(days=181)
        frappe.db.sql(
            """
            UPDATE `tabAOS User Activity`
            SET occurred_at=%s, last_occurrence_at=%s
            WHERE public_id IN %s
            """,
            (old, old, (first, second)),
        )
        removed = ActivityService.purge_expired(limit=1)
        self.assertEqual(removed, 1)
        self.assertEqual(
            frappe.db.count("AOS User Activity", {"public_id": ["in", [first, second]]}),
            1,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
