from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.social.block import block_user_impl, list_blocked_users_impl, unblock_user_impl
from aos.api.social.lists import get_followers_impl, get_following_impl
from aos.api.social.relationship import get_relationship_status_impl
from aos.api.social.search_users import search_users_impl
from aos.api.social.toggle_follow import toggle_follow_impl
from aos.services.account_deletion_service import _remove_social_graph
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.social.service import SocialService
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestSocialAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("social")
        self.created_users = []
        frappe.set_user("Administrator")
        self.actor = self.make_user("actor")
        self.target = self.make_user("target")
        frappe.set_user(self.actor)

    def tearDown(self):
        frappe.set_user("Administrator")
        email_like = f"{self.prefix}-%@example.com"
        frappe.db.sql("DELETE FROM `tabAOS Follow` WHERE follower_user LIKE %s OR following_user LIKE %s", (email_like, email_like))
        self.cleanup_feature_rows()

    def _toggle(self, **kwargs):
        with (
            patch("aos.api.social.toggle_follow.rate_limit", return_value=None),
            patch("aos.services.social.service.SocialService._notify_follow_atomic", return_value="test"),
        ):
            return toggle_follow_impl(**kwargs)

    def _block(self, **kwargs):
        with (
            patch("aos.api.social.block.rate_limit", return_value=None),
            patch("aos.api.social.block.record_block_user_activity"),
        ):
            return block_user_impl(**kwargs)

    def test_guest_access_is_denied(self):
        frappe.set_user("Guest")
        response = self._toggle(account_id=public_account_id_for_user(self.target), action="follow")
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "UNAUTHORIZED")

    def test_self_block_is_rejected(self):
        response = self._block(account_id=public_account_id_for_user(self.actor))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "SOCIAL_SELF_ACTION")

    def test_unknown_fields_and_alias_conflicts_are_rejected(self):
        unknown = self._toggle(account_id=public_account_id_for_user(self.target), unexpected=1)
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["error"], "SOCIAL_UNKNOWN_FIELD")

        conflict = self._toggle(target_user=self.target, account_id=public_account_id_for_user(self.actor))
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["error"], "SOCIAL_ALIAS_CONFLICT")

    def test_self_follow_is_rejected(self):
        response = self._toggle(account_id=public_account_id_for_user(self.actor))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "SOCIAL_SELF_ACTION")

    def test_explicit_follow_and_unfollow_are_idempotent(self):
        account_id = public_account_id_for_user(self.target)
        first = self._toggle(account_id=account_id, action="follow")
        second = self._toggle(account_id=account_id, action="follow")
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertTrue(first["data"]["changed"])
        self.assertFalse(second["data"]["changed"])
        self.assertEqual(frappe.db.count("AOS Follow", {"follower_user": self.actor, "following_user": self.target}), 1)

        removed = self._toggle(account_id=account_id, action="unfollow")
        removed_again = self._toggle(account_id=account_id, action="unfollow")
        self.assertTrue(removed["data"]["changed"])
        self.assertFalse(removed_again["data"]["changed"])
        self.assertEqual(frappe.db.count("AOS Follow", {"follower_user": self.actor, "following_user": self.target}), 0)

    def test_mutual_follow_is_friends_and_lists_do_not_leak_internal_user_id(self):
        self._toggle(account_id=public_account_id_for_user(self.target), action="follow")
        frappe.set_user(self.target)
        self._toggle(account_id=public_account_id_for_user(self.actor), action="follow")
        frappe.set_user(self.actor)
        with patch("aos.api.social.relationship.rate_limit", return_value=None):
            relation = get_relationship_status_impl(account_id=public_account_id_for_user(self.target))
        self.assertTrue(relation["data"]["is_friend"])

        with patch("aos.api.social.lists.rate_limit", return_value=None):
            following = get_following_impl(limit=10)
            followers = get_followers_impl(limit=10)
        self.assertEqual(following["data"]["items"][0]["account_id"], public_account_id_for_user(self.target))
        self.assertNotIn(self.target, str(following["data"]["items"]))
        self.assertNotIn(self.target, str(followers["data"]["items"]))

    def test_block_is_idempotent_and_atomically_removes_both_follow_edges(self):
        self._toggle(account_id=public_account_id_for_user(self.target), action="follow")
        frappe.set_user(self.target)
        self._toggle(account_id=public_account_id_for_user(self.actor), action="follow")
        frappe.set_user(self.actor)

        first = self._block(account_id=public_account_id_for_user(self.target), reason="spam")
        second = self._block(account_id=public_account_id_for_user(self.target), reason="spam")
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(frappe.db.count("AOS Follow", {"follower_user": ["in", [self.actor, self.target]]}), 0)
        self.assertEqual(
            frappe.db.count("AOS User Block", {"blocker_user": self.actor, "blocked_user": self.target, "status": "Active"}),
            1,
        )
        self.assertFalse(second["data"]["changed"])

    def test_blocked_users_are_excluded_from_discovery_and_social_lists(self):
        self._toggle(account_id=public_account_id_for_user(self.target), action="follow")
        self._block(account_id=public_account_id_for_user(self.target))
        with patch("aos.api.social.lists.rate_limit", return_value=None):
            listing = get_following_impl(limit=10)
        self.assertEqual(listing["data"]["items"], [])
        with patch("aos.api.social.search_users.rate_limit", return_value=None):
            search = search_users_impl(query="Feature Target")
        self.assertEqual(search["data"]["items"], [])

    def test_unblock_and_blocked_list_cursor_validation(self):
        self._block(account_id=public_account_id_for_user(self.target))
        with patch("aos.api.social.block.rate_limit", return_value=None):
            listing = list_blocked_users_impl(limit=1)
            malformed = list_blocked_users_impl(limit=1, cursor="tampered")
            unblocked = unblock_user_impl(account_id=public_account_id_for_user(self.target))
            repeated = unblock_user_impl(account_id=public_account_id_for_user(self.target))
        self.assertEqual(len(listing["data"]["items"]), 1)
        self.assertTrue(listing["data"]["items"][0]["id"].startswith("BLK-"))
        self.assertEqual(malformed["error"], "SOCIAL_INVALID_CURSOR")
        self.assertTrue(unblocked["data"]["changed"])
        self.assertFalse(repeated["data"]["changed"])

    def test_search_bounds_and_private_serializer(self):
        with patch("aos.api.social.search_users.rate_limit", return_value=None):
            too_short = search_users_impl(query="x")
            valid = search_users_impl(query="Feature Target", limit=10)
        self.assertEqual(too_short["error"], "SOCIAL_INVALID_SEARCH")
        self.assertTrue(valid["ok"], valid)
        self.assertTrue(all("@example.com" not in str(item) for item in valid["data"]["items"]))

    def test_suspended_target_is_not_discoverable_or_followable(self):
        frappe.db.set_value("AOS Profile", self.target, "account_status", "Suspended", update_modified=False)
        response = self._toggle(account_id=public_account_id_for_user(self.target), action="follow")
        self.assertEqual(response["error"], "SOCIAL_PROFILE_UNAVAILABLE")
        with patch("aos.api.social.search_users.rate_limit", return_value=None):
            search = search_users_impl(query="Feature Target")
        self.assertEqual(search["data"]["items"], [])

    def test_notification_outbox_failure_rolls_back_follow_and_notification(self):
        with (
            patch("aos.api.social.toggle_follow.rate_limit", return_value=None),
            patch(
                "aos.services.social.service.create_notification_delivery_job",
                side_effect=RuntimeError("outbox unavailable"),
            ),
        ):
            response = toggle_follow_impl(
                account_id=public_account_id_for_user(self.target),
                action="follow",
            )
        self.assertEqual(response["error"], "SOCIAL_INTERNAL_ERROR")
        self.assertFalse(
            frappe.db.exists(
                "AOS Follow",
                {"follower_user": self.actor, "following_user": self.target},
            )
        )
        self.assertFalse(
            frappe.db.exists(
                "AOS Notification",
                {"user": self.target, "actor": self.actor, "type": "follow"},
            )
        )

    def test_cursor_pagination_is_deterministic_and_non_overlapping(self):
        targets = [self.target, self.make_user("page-two"), self.make_user("page-three")]
        for target in targets:
            self._toggle(account_id=public_account_id_for_user(target), action="follow")

        with patch("aos.api.social.lists.rate_limit", return_value=None):
            first = get_following_impl(limit=1)
            second = get_following_impl(limit=1, cursor=first["data"]["next_cursor"])

        self.assertTrue(first["data"]["has_more"])
        self.assertTrue(first["data"]["next_cursor"])
        first_id = first["data"]["items"][0]["account_id"]
        second_id = second["data"]["items"][0]["account_id"]
        self.assertNotEqual(first_id, second_id)

    def test_blocked_relationship_neutralizes_graph_state_in_both_directions(self):
        self._toggle(account_id=public_account_id_for_user(self.target), action="follow")
        self._block(account_id=public_account_id_for_user(self.target))

        with patch("aos.api.social.relationship.rate_limit", return_value=None):
            actor_view = get_relationship_status_impl(account_id=public_account_id_for_user(self.target))
        frappe.set_user(self.target)
        with patch("aos.api.social.relationship.rate_limit", return_value=None):
            target_view = get_relationship_status_impl(account_id=public_account_id_for_user(self.actor))

        for response in (actor_view, target_view):
            self.assertFalse(response["data"]["is_following"])
            self.assertFalse(response["data"]["is_followed_by"])
            self.assertFalse(response["data"]["is_friend"])
            self.assertFalse(response["data"]["can_message"])
            self.assertFalse(response["data"]["can_call"])

    def test_follow_notification_deduplication_does_not_create_rows(self):
        service = SocialService()
        with (
            patch.object(service.repository, "recent_follow_notification_exists", return_value=True),
            patch("aos.services.social.service.frappe.get_doc") as get_doc,
            patch("aos.services.social.service.create_notification_delivery_job") as create_job,
        ):
            result = service._notify_follow_atomic(recipient=self.target, actor=self.actor)
        self.assertEqual(result, "deduplicated")
        get_doc.assert_not_called()
        create_job.assert_not_called()

    def test_account_deletion_social_cleanup_is_bounded_and_complete(self):
        self._toggle(account_id=public_account_id_for_user(self.target), action="follow")
        frappe.set_user(self.target)
        self._toggle(account_id=public_account_id_for_user(self.actor), action="follow")
        frappe.set_user(self.actor)
        self._block(account_id=public_account_id_for_user(self.target))

        summary = _remove_social_graph(user=self.actor)

        self.assertGreaterEqual(summary["follow_rows_removed"], 0)
        self.assertEqual(
            frappe.db.count(
                "AOS Follow",
                filters={"follower_user": ["in", [self.actor, self.target]]},
            ),
            0,
        )
        self.assertEqual(
            frappe.db.count(
                "AOS User Block",
                filters={
                    "blocker_user": self.actor,
                    "blocked_user": self.target,
                    "status": "Active",
                },
            ),
            0,
        )
        self.assertGreaterEqual(summary["active_social_blocks_closed"], 1)

