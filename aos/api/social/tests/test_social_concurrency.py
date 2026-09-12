from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from frappe.tests import IntegrationTestCase

from aos.services.social.repository import SocialRepository


class SocialConcurrencyContracts(IntegrationTestCase):
    """Database-bound concurrency invariants exercised on the staging Frappe runtime."""

    def test_pair_lock_is_deterministic_and_row_scoped(self):
        repository = SocialRepository()
        with patch("aos.services.social.repository.frappe.db.sql", return_value=[]) as sql:
            repository.lock_account_pair(user_a="z-user@example.test", user_b="a-user@example.test")

        query, params = sql.call_args.args
        normalized = " ".join(query.upper().replace("`", "").split())
        self.assertIn("FROM TABUSER", normalized)
        self.assertIn("ORDER BY NAME ASC FOR UPDATE", normalized)
        self.assertEqual(params["users"], ("a-user@example.test", "z-user@example.test"))

    def test_duplicate_follow_insert_race_returns_database_winner(self):
        repository = SocialRepository()
        candidate = MagicMock()
        candidate.insert.side_effect = RuntimeError("simulated duplicate race")

        with (
            patch("aos.services.social.repository.frappe.get_doc", return_value=candidate),
            patch("aos.services.social.repository.is_duplicate_entry_error", return_value=True),
            patch("aos.services.social.repository.frappe.db.get_value", return_value="FOLLOW-WINNER"),
        ):
            name, changed = repository.insert_follow(
                follower="first@example.test",
                target="second@example.test",
            )

        self.assertEqual(name, "FOLLOW-WINNER")
        self.assertFalse(changed)
        candidate.insert.assert_called_once_with(ignore_permissions=True)

    def test_reciprocal_follow_transition_updates_friend_count_once_per_account(self):
        repository = SocialRepository()
        with (
            patch.object(repository, "follow_exists", return_value=True),
            patch.object(repository, "_adjust_profile_counter") as adjust,
        ):
            repository.apply_follow_insert_counters(
                follower="first@example.test",
                target="second@example.test",
            )

        self.assertEqual(
            adjust.call_args_list,
            [
                call("second@example.test", "total_followers", 1),
                call("first@example.test", "total_following", 1),
                call("first@example.test", "total_friends", 1),
                call("second@example.test", "total_friends", 1),
            ],
        )

    def test_one_way_follow_does_not_increment_friend_count(self):
        repository = SocialRepository()
        with (
            patch.object(repository, "follow_exists", return_value=False),
            patch.object(repository, "_adjust_profile_counter") as adjust,
        ):
            repository.apply_follow_insert_counters(
                follower="first@example.test",
                target="second@example.test",
            )

        self.assertEqual(
            adjust.call_args_list,
            [
                call("second@example.test", "total_followers", 1),
                call("first@example.test", "total_following", 1),
            ],
        )

    def test_block_cleanup_of_mutual_follow_decrements_friend_count_once(self):
        repository = SocialRepository()
        rows = [
            SimpleNamespace(
                name="FOLLOW-A-B",
                follower_user="first@example.test",
                following_user="second@example.test",
            ),
            SimpleNamespace(
                name="FOLLOW-B-A",
                follower_user="second@example.test",
                following_user="first@example.test",
            ),
        ]
        with (
            patch("aos.services.social.repository.frappe.db.sql", side_effect=[rows, None]),
            patch.object(repository, "_adjust_profile_counter") as adjust,
        ):
            removed = repository.remove_follows_both_directions(
                user_a="first@example.test",
                user_b="second@example.test",
            )

        self.assertEqual(removed, 2)
        self.assertEqual(
            adjust.call_args_list,
            [
                call("second@example.test", "total_followers", -1),
                call("first@example.test", "total_following", -1),
                call("first@example.test", "total_followers", -1),
                call("second@example.test", "total_following", -1),
                call("first@example.test", "total_friends", -1),
                call("second@example.test", "total_friends", -1),
            ],
        )
