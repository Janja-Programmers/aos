from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


class TestSocialStaticContracts:
    def test_single_canonical_social_document(self):
        docs = sorted(path.name for path in (ROOT / "docs/features/social").glob("*.md"))
        assert docs == ["README.md"]
        source = (ROOT / "ci/validate_api_documentation.py").read_text()
        assert '"social": "docs/features/social/README.md"' in source
        assert '"social": "docs/features/social/README.md",' in source[source.index("SINGLE_FILE_FEATURE_DOCS"):]

    def test_public_v1_surface_is_explicit_and_has_no_toggle(self):
        source = (ROOT / "aos/api/v1/social/__init__.py").read_text()
        endpoints = (
            "follow", "unfollow", "get_relationship_status", "get_following", "get_followers",
            "get_friends", "search_users", "block_user", "unblock_user", "get_block_status",
            "list_blocked_users",
        )
        for endpoint in endpoints:
            assert f"def {endpoint}" in source
        assert "def toggle_follow" not in source
        assert source.count("**_client_kwargs(kwargs)") == len(endpoints)
        assert not (ROOT / "aos/api/social/toggle_follow.py").exists()

    def test_request_contract_has_no_legacy_aliases_or_offsets(self):
        constants = (ROOT / "aos/services/social/constants.py").read_text()
        validation = (ROOT / "aos/services/social/validation.py").read_text()
        assert 'TARGET_FIELDS = frozenset({"account_id"})' in constants
        assert 'LIST_FIELDS = frozenset({"limit", "cursor", "search"})' in constants
        assert 'SEARCH_FIELDS = frozenset({"query", "limit", "cursor"})' in constants
        for legacy in ('"target_user"', '"action"', '"start"', '"offset"'):
            assert legacy not in constants
        assert "resolve_account_reference(account_id)" in validation
        assert "normalize_public_account_id(raw)" in validation

    def test_capability_service_is_internal_boundary_and_accounts_consumes_it(self):
        capabilities = (ROOT / "aos/services/social/capabilities.py").read_text()
        accounts = (ROOT / "aos/services/accounts/profile_service.py").read_text()
        assert "class SocialCapabilityService" in capabilities
        for method in ("relationship_projection", "projection_map", "is_blocked", "can_view_profile", "can_follow", "can_message", "can_call"):
            assert f"def {method}" in capabilities
        assert "SocialCapabilityService().relationship_projection" in accounts
        assert "build_relationship_status" not in accounts
        assert "from aos.api.social" not in accounts

    def test_accounts_friend_count_is_denormalized_without_social_count_query(self):
        repo = (ROOT / "aos/services/accounts/repository.py").read_text()
        serializers = (ROOT / "aos/services/accounts/serializers.py").read_text()
        profile = json.loads((ROOT / "aos/aos/doctype/aos_profile/aos_profile.json").read_text())
        fields = {field.get("fieldname"): field for field in profile["fields"]}
        assert "total_friends" in fields
        assert fields["total_friends"].get("read_only") == 1
        assert "COALESCE(p.total_friends, 0) AS total_friends" in repo
        assert 'to_non_negative_int(_get(row, "total_friends"))' in serializers
        assert "SocialRepository" not in serializers
        assert "_friends_count" not in serializers

    def test_relationship_serializers_use_accounts_identity_not_live(self):
        source = (ROOT / "aos/services/social/serializers.py").read_text()
        assert "serialize_internal_identity_map" in source
        assert "user_display" not in source
        assert "is_live" not in source
        assert '"account_id"' in source
        assert '"target_user"' not in source

    def test_graph_mutations_are_pair_locked_and_counter_deltas_are_atomic(self):
        repo = (ROOT / "aos/services/social/repository.py").read_text()
        service = (ROOT / "aos/services/social/service.py").read_text()
        assert "ORDER BY name ASC\n            FOR UPDATE" in repo
        assert "self.repository.lock_account_pair" in service
        assert "GREATEST(COALESCE(`{field}`, 0) + %s, 0)" in repo
        assert "apply_follow_insert_counters" in repo
        assert "apply_follow_delete_counters" in repo
        # Normal API mutations use O(1) deltas; reconciliation is not called from SocialService.
        assert "sync_counters(" not in service

    def test_all_pair_mutations_share_the_same_lock_boundary(self):
        service = (ROOT / "aos/services/social/service.py").read_text()
        set_follow = service[service.index("def _set_follow("):service.index("def relationship(")]
        block = service[service.index("def block("):service.index("def unblock(")]
        unblock = service[service.index("def unblock("):service.index("def block_status(")]

        assert set_follow.index("lock_account_pair") < set_follow.index("insert_follow")
        assert set_follow.index("lock_account_pair") < set_follow.index("delete_follow")
        assert block.index("lock_account_pair") < block.index("activate_block")
        assert unblock.index("lock_account_pair") < unblock.index("deactivate_block")

        concurrency = (ROOT / "aos/api/social/tests/test_social_concurrency.py").read_text()
        for invariant in (
            "test_pair_lock_is_deterministic_and_row_scoped",
            "test_duplicate_follow_insert_race_returns_database_winner",
            "test_reciprocal_follow_transition_updates_friend_count_once_per_account",
            "test_block_cleanup_of_mutual_follow_decrements_friend_count_once",
        ):
            assert invariant in concurrency

    def test_friendship_is_mutual_follow_not_separate_request_model(self):
        repo = (ROOT / "aos/services/social/repository.py").read_text()
        docs = (ROOT / "docs/features/social/README.md").read_text()
        assert "INNER JOIN `tabAOS Follow` f2" in repo
        assert "There are no friend requests" in docs
        assert "AOS Friend" not in repo

    def test_block_model_has_one_directional_pair_without_active_pair_key(self):
        model = json.loads((ROOT / "aos/aos/doctype/aos_user_block/aos_user_block.json").read_text())
        names = {field.get("fieldname") for field in model["fields"]}
        installer = (ROOT / "aos/patches/v1_0/install_social_indexes.py").read_text()
        assert "active_pair_key" not in names
        assert '("blocker_user", "blocked_user")' in installer
        assert "uq_social_block_pair" in installer
        assert "active_pair_key" not in installer

    def test_fresh_site_schema_installer_replaces_legacy_social_patch(self):
        patches = (ROOT / "aos/patches.txt").read_text()
        migrate = (ROOT / "aos/migrate.py").read_text()
        installer = (ROOT / "aos/patches/v1_0/install_social_indexes.py").read_text()
        assert "aos.patches.v1_0.install_social_indexes" in patches
        assert "harden_social_subsystem" not in patches
        assert not (ROOT / "aos/patches/v1_0/harden_social_subsystem.py").exists()
        assert "install_social_indexes.execute" in migrate
        assert "frappe.db.commit" not in installer
        assert "DELETE FROM" not in installer
        assert "UPDATE `tab" not in installer

    def test_shared_legacy_constraint_patch_no_longer_migrates_social(self):
        source = (ROOT / "aos/patches/v1_0/add_unique_constraints.py").read_text()
        assert "AOS Follow" not in source
        assert "AOS User Block" not in source
        assert "_dedupe_social_follows" not in source
        assert "_normalize_user_block_active_keys" not in source

    def test_social_indexes_cover_uniqueness_and_keyset_access(self):
        source = (ROOT / "aos/patches/v1_0/install_social_indexes.py").read_text()
        for index in (
            "uq_social_follow_pair", "idx_social_following_page", "idx_social_follower_page",
            "idx_social_follow_reverse", "uq_social_block_pair", "idx_social_blocker_page",
            "idx_social_blocked_lookup", "idx_social_profile_discovery",
        ):
            assert index in source

    def test_relationship_lists_and_search_are_keyset_only_and_bounded(self):
        repo = (ROOT / "aos/services/social/repository.py").read_text()
        service = (ROOT / "aos/services/social/service.py").read_text()
        assert "OFFSET" not in repo.upper()
        assert '"limit": limit' in service
        assert '"has_more": has_more' in service
        assert '"next_cursor": next_cursor' in service
        assert "len(rows) > limit" in service
        assert "LIMIT %(limit)s" in repo
        assert "COUNT(*)" not in repo[repo.index("def list_social"):repo.index("def list_active_followers_for_event_page")]

    def test_discovery_uses_prefix_accounts_fields_not_user_email(self):
        repo = (ROOT / "aos/services/social/repository.py").read_text()
        section = repo[repo.index("def search_users"):repo.index("def list_blocked")]
        assert "safe_like_prefix(query)" in section
        assert "p.display_name LIKE" in section
        assert "p.name LIKE" in section
        assert "u.email" not in section
        assert "u.full_name" not in section

    def test_block_precedence_removes_both_follow_edges_and_hides_incoming_block(self):
        repo = (ROOT / "aos/services/social/repository.py").read_text()
        service = (ROOT / "aos/services/social/service.py").read_text()
        serializers = (ROOT / "aos/services/social/serializers.py").read_text()
        assert "remove_follows_both_directions" in service
        assert "DELETE FROM `tabAOS Follow`" in repo
        assert "_public_relationship_projection" in service
        assert 'raise SocialNotFoundError("Profile unavailable.")' in service
        assert '"has_blocked_me": False' in service
        assert '"can_view_profile": not blocked' in serializers

    def test_permanent_purge_deletes_social_private_rows_and_reconciles(self):
        source = (ROOT / "aos/services/account_purge_service.py").read_text()
        section = source[source.index("def _purge_social_batch"):source.index("def _purge_wishlist_batch")]
        assert "DELETE FROM `tabAOS Follow`" in section
        assert "DELETE FROM `tabAOS User Block`" in section
        assert "repository.sync_counters" in section
        assert '"social_block_rows_removed"' in section
        remaining = source[source.index("def _remaining_bounded_private_rows"):]
        assert '("AOS User Block", "blocker_user = %s OR blocked_user = %s"' in remaining

    def test_follow_notification_uses_canonical_notifications_and_is_failure_isolated(self):
        service = (ROOT / "aos/services/social/service.py").read_text()
        assert "NotificationService.notify_follow" in service
        assert 'dedupe_key=f"social:follow:{follow_name}:{recipient}"' in service
        assert "recent_follow_notification_exists" in service
        assert 'return "notification_failed"' in service
        # No Social-owned transport/delivery implementation.
        social_files = "\n".join(path.read_text(errors="ignore") for path in (ROOT / "aos/services/social").glob("*.py"))
        assert "send_push" not in social_files
        assert "send_email" not in social_files

    def test_notification_is_follow_only_not_block_or_unfollow(self):
        service = (ROOT / "aos/services/social/service.py").read_text()
        unfollow = service[service.index("def unfollow"):service.index("def relationship(")]
        block = service[service.index("def block("):service.index("def unblock(")]
        assert "NotificationService" not in unfollow
        assert "NotificationService" not in block

    def test_social_observability_has_no_legacy_toggle_or_alias_reason(self):
        source = (ROOT / "aos/services/social/observability.py").read_text()
        assert '"toggle_follow"' not in source
        assert '"alias_conflict"' not in source
        assert 'frappe.logger("aos.social"' in source

    def test_rate_limit_registry_matches_final_public_surface(self):
        data = json.loads((ROOT / "ci/public-endpoint-rate-limits.json").read_text())
        rows = data if isinstance(data, list) else data["endpoints"]
        endpoints = {row["endpoint"] for row in rows}
        assert "aos.api.v1.social.__init__.follow" in endpoints
        assert "aos.api.v1.social.__init__.unfollow" in endpoints
        assert "aos.api.v1.social.__init__.toggle_follow" not in endpoints

    def test_no_social_compatibility_language_or_modules_remain(self):
        roots = [ROOT / "aos/services/social", ROOT / "aos/api/social"]
        text = "\n".join(path.read_text(errors="ignore") for root in roots for path in root.rglob("*.py") if "tests" not in path.parts)
        for obsolete in ("toggle_follow", "active_pair_key", "SOCIAL_ALIAS_CONFLICT"):
            assert obsolete not in text

    def test_social_services_do_not_commit(self):
        paths = [
            ROOT / "aos/services/social/service.py",
            ROOT / "aos/services/social/repository.py",
            ROOT / "aos/patches/v1_0/install_social_indexes.py",
        ]
        for path in paths:
            assert "frappe.db.commit" not in path.read_text()
