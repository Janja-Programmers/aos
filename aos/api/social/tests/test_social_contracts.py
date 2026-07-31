from __future__ import annotations

from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[4]


class TestSocialStaticContracts(TestCase):
    def test_canonical_domain_structure_exists(self):
        expected = {
            "api.py", "service.py", "repository.py", "validation.py", "policy.py",
            "serializers.py", "constants.py", "errors.py", "observability.py",
        }
        root = ROOT / "aos/services/social"
        self.assertTrue(expected.issubset({path.name for path in root.glob("*.py")}))

    def test_social_services_do_not_commit(self):
        for relative in (
            "aos/services/social/service.py",
            "aos/services/social/repository.py",
            "aos/patches/v1_0/harden_social_subsystem.py",
        ):
            source = (ROOT / relative).read_text()
            self.assertNotIn("frappe.db.commit()", source, relative)

    def test_public_wrappers_remain_stable(self):
        source = (ROOT / "aos/api/v1/social/__init__.py").read_text()
        for endpoint in (
            "toggle_follow", "get_relationship_status", "get_following", "get_followers",
            "get_friends", "search_users", "block_user", "unblock_user",
            "get_block_status", "list_blocked_users",
        ):
            self.assertIn(f"def {endpoint}", source)

    def test_migration_is_registered_bounded_and_has_no_commit(self):
        patches = (ROOT / "aos/patches.txt").read_text()
        source = (ROOT / "aos/patches/v1_0/harden_social_subsystem.py").read_text()
        legacy = (ROOT / "aos/patches/v1_0/add_unique_constraints.py").read_text()
        self.assertIn("aos.patches.v1_0.harden_social_subsystem", patches)
        self.assertNotIn("frappe.db.commit()", source)
        self.assertNotIn("frappe.db.commit()", legacy)
        self.assertIn("LIMIT %s", source)
        self.assertIn("_remove_invalid_follows", source)
        self.assertIn("LEFT JOIN `tabUser` blocker", source)
        self.assertIn("_SOCIAL_BATCH", legacy)
        self.assertNotIn("GROUP_CONCAT(name", source)

    def test_queries_are_parameterized_and_bounded(self):
        source = (ROOT / "aos/services/social/repository.py").read_text()
        self.assertNotIn(".format(", source)
        self.assertIn("LIMIT %(limit)s", source)
        self.assertIn("safe_like_contains", source)
        self.assertIn("ORDER BY", source)
        self.assertIn("LIMIT 2 FOR UPDATE", source)
        self.assertIn("range(0, len(unique), 200)", source)

    def test_rate_limit_coverage(self):
        for relative in (
            "aos/api/social/toggle_follow.py",
            "aos/api/social/relationship.py",
            "aos/api/social/lists.py",
            "aos/api/social/search_users.py",
            "aos/api/social/block.py",
        ):
            self.assertIn("rate_limit(", (ROOT / relative).read_text(), relative)

    def test_account_deletion_keeps_bounded_social_cleanup(self):
        source = (ROOT / "aos/services/account_deletion_service.py").read_text()
        self.assertIn("_remove_social_graph", source)
        self.assertIn("AOS Follow", source)
        self.assertIn("LIMIT %s", source)
        self.assertIn("active_social_blocks_closed", source)
        self.assertIn("WHERE name = %s FOR UPDATE", source)

    def test_database_constraints_are_the_duplicate_boundary(self):
        follow = (ROOT / "aos/aos/doctype/aos_follow/aos_follow.py").read_text()
        block = (ROOT / "aos/aos/doctype/aos_user_block/aos_user_block.py").read_text()
        model = (ROOT / "aos/aos/doctype/aos_user_block/aos_user_block.json").read_text()
        self.assertNotIn("_validate_unique_follow", follow)
        self.assertNotIn("_validate_unique_active_block", block)
        self.assertIn('"length": 300', model)

    def test_cross_feature_social_payloads_use_public_ids(self):
        notification = (ROOT / "aos/services/notification_service.py").read_text()
        live = (ROOT / "aos/api/live/live.py").read_text()
        self.assertIn("public_account_id_for_user(actor)", notification)
        self.assertIn("public_account_id_for_user(host_user)", notification)
        self.assertIn("host_public_id", live)

    def test_legacy_dynamic_sql_helpers_remain_available(self):
        source = (ROOT / "aos/api/social/search_users.py").read_text()
        for helper in ("_not_blocked_sql", "_search_like", "_prefix_like"):
            self.assertIn(f"def {helper}", source)


def test_v1_social_strips_frappe_transport_metadata_only() -> None:
    source = (ROOT / "aos/api/v1/social/__init__.py").read_text()
    assert "from aos.api.v1._transport import client_kwargs as _client_kwargs" in source
    assert source.count("**_client_kwargs(kwargs)") == 10


def test_social_user_serialization_uses_batched_identity_reads() -> None:
    display = (ROOT / "aos/api/shared/user_display.py").read_text()
    accounts = (ROOT / "aos/services/accounts/serializers.py").read_text()
    assert "serialize_internal_identity_map(unique)" in display
    assert "WHERE u.name IN %(users)s" in accounts
    assert "get_public_url_map(media_ids)" in accounts


def test_pair_mutations_share_a_deterministic_lock_boundary() -> None:
    repository = (ROOT / "aos/services/social/repository.py").read_text()
    service = (ROOT / "aos/services/social/service.py").read_text()
    follow = (ROOT / "aos/aos/doctype/aos_follow/aos_follow.py").read_text()
    block = (ROOT / "aos/aos/doctype/aos_user_block/aos_user_block.py").read_text()
    assert "def lock_account_pair" in repository
    assert "ORDER BY name ASC" in repository and "FOR UPDATE" in repository
    assert service.count("lock_account_pair(user_a=actor, user_b=target)") == 3
    assert service.count("self.policy.require_actor(actor)") >= 7
    assert "_validate_not_blocked" in follow
    assert "_enforce_active_block_side_effects" in block


def test_cross_feature_follower_event_fanout_is_bounded_and_block_aware() -> None:
    repository = (ROOT / "aos/services/social/repository.py").read_text()
    notifications = (ROOT / "aos/services/notification_service.py").read_text()
    live = (ROOT / "aos/api/live/live.py").read_text()
    realtime = (ROOT / "aos/api/live/realtime.py").read_text()
    assert "def list_active_followers_for_event" in repository
    assert "NOT EXISTS" in repository and "LIMIT %(limit)s" in repository
    assert "MAX_SOCIAL_EVENT_FANOUT = 500" in (ROOT / "aos/services/social/constants.py").read_text()
    for source in (notifications, live, realtime):
        assert "list_active_followers_for_event" in source


def test_social_failure_logs_do_not_include_tracebacks_or_private_values() -> None:
    activity = (ROOT / "aos/api/social/activity.py").read_text()
    boundary = (ROOT / "aos/services/social/api.py").read_text()
    assert "frappe.get_traceback()" not in activity
    assert "Social activity hook failed." in activity
    assert "Social API operation failed." in boundary

def test_social_package_does_not_eagerly_import_service() -> None:
    source = (ROOT / "aos/services/social/__init__.py").read_text()
    assert "if TYPE_CHECKING:" in source
    assert "def __getattr__(name: str)" in source
    assert "from .service import SocialService\n\n__all__" not in source

