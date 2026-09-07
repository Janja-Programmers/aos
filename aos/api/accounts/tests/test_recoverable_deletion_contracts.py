from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


class TestRecoverableDeletionContracts(unittest.TestCase):
    def _source(self, relative: str) -> str:
        return (ROOT / relative).read_text()

    def test_tombstone_preserves_durable_domains_and_only_revokes_ephemeral_state(self):
        source = self._source("aos/services/account_deletion_service.py")
        block = source.split("def tombstone_deleted_account_features", 1)[1].split(
            "def restore_deleted_account_features", 1
        )[0]
        for marker in (
            "social_graph_preserved",
            "marketplace_state_preserved",
            "private_personalization_preserved",
            "verification_state_preserved",
            "push_tokens_removed",
            "pending_media_uploads_cancelled",
        ):
            self.assertIn(marker, block)
        for forbidden in (
            "_purge_social_batch",
            "_cleanup_chat_private_state",
            "_cleanup_report_account_data",
            "_cleanup_review_account_data",
            "_cleanup_activity_account_data",
            "_cleanup_verification_documents",
        ):
            self.assertNotIn(forbidden, block)
        self.assertNotIn("frappe.db.commit", block)

    def test_pending_upload_invalidation_does_not_touch_durable_media(self):
        source = self._source("aos/services/account_deletion_service.py")
        block = source.split("def _cancel_pending_media_uploads", 1)[1].split(
            "def _cleanup_verification_documents", 1
        )[0]
        self.assertIn("status = 'Initialized'", block)
        self.assertIn("failure_code = 'ACCOUNT_DELETED'", block)
        self.assertIn("staging_cleanup_required = 1", block)
        self.assertNotIn("release_media", block)
        self.assertNotIn("delete_media", block)

    def test_permanent_purge_is_bounded_resumable_and_scheduler_isolates_accounts(self):
        purge = self._source("aos/services/account_purge_service.py")
        task = self._source("aos/tasks/accounts.py")
        constants = self._source("aos/services/accounts/constants.py")
        self.assertIn("ACCOUNT_PURGE_SOCIAL_EDGE_BATCH_SIZE = 10_000", constants)
        self.assertIn("LIMIT %s FOR UPDATE", purge)
        self.assertIn("_remaining_bounded_private_rows", purge)
        for helper in (
            "_purge_social_batch",
            "_purge_chat_private_batch",
            "_purge_reports_private_batch",
            "_purge_reviews_private_batch",
            "_purge_activity_private_batch",
        ):
            self.assertIn(f"def {helper}", purge)
        self.assertIn("frappe.db.savepoint(savepoint)", task)
        self.assertIn("frappe.db.rollback(save_point=savepoint)", task)
        self.assertNotIn("frappe.get_traceback", task)
        self.assertNotIn("frappe.db.commit", purge)
        self.assertNotIn("frappe.db.commit", task)

    def test_profile_has_durable_purge_progress_and_old_seller_snapshot_is_removed(self):
        profile = json.loads((ROOT / "aos/aos/doctype/aos_profile/aos_profile.json").read_text())
        profile_fields = {field.get("fieldname") for field in profile.get("fields", [])}
        self.assertTrue({"purge_status", "purge_started_at", "purge_completed_at"} <= profile_fields)
        seller = json.loads((ROOT / "aos/aos/doctype/aos_seller/aos_seller.json").read_text())
        seller_fields = {field.get("fieldname") for field in seller.get("fields", [])}
        self.assertNotIn("account_delete_previous_status", seller_fields)
        self.assertNotIn(
            "restore_seller_after_account_restore",
            self._source("aos/services/sellers/policy.py"),
        )


    def test_preserved_marketplace_rows_are_hidden_in_secondary_public_paths(self):
        for relative in (
            "aos/api/ads/list_ads.py",
            "aos/api/ads/get_ad.py",
            "aos/api/ads/image_search.py",
            "aos/api/wishlist/list.py",
            "aos/api/search_ranking/recommendations.py",
        ):
            source = self._source(relative)
            self.assertIn("enabled = 1", source, relative)
            self.assertNotIn("p.is_deleted", source, relative)
            self.assertNotIn("profile.is_deleted", source, relative)
            self.assertIn("account_status", source, relative)

    def test_hourly_purge_job_is_registered(self):
        hooks = self._source("aos/hooks.py")
        self.assertIn("aos.tasks.accounts.purge_expired_deleted_accounts", hooks)
