from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]


class TestReviewsContracts(unittest.TestCase):
    def test_v1_wrappers_strip_framework_transport_metadata(self):
        source = (ROOT / "aos/api/v1/reviews/__init__.py").read_text()
        self.assertIn("client_kwargs(kwargs)", source)
        self.assertNotIn("frappe.db", source)

    def test_public_serializer_has_no_private_identity_fields(self):
        source = (ROOT / "aos/services/reviews/serializers.py").read_text()
        for forbidden in ("email\"", "phone\"", "session", "reporter", "object_key", "bucket"):
            self.assertNotIn(forbidden, source)

    def test_moderation_uses_generation_guard(self):
        source = (ROOT / "aos/services/moderation_service.py").read_text()
        self.assertIn('context.get("moderation_generation")', source)
        self.assertIn("job_generation != current_generation", source)

    def test_reviews_patch_is_registered_and_does_not_commit(self):
        patches = (ROOT / "aos/patches.txt").read_text()
        self.assertIn("aos.patches.v1_0.harden_reviews_subsystem", patches)
        source = (ROOT / "aos/patches/v1_0/harden_reviews_subsystem.py").read_text()
        self.assertNotIn("frappe.db.commit", source)
        self.assertIn("status = 'Withdrawn'", source)

    def test_review_doctype_exposes_explicit_lifecycle(self):
        schema = json.loads((ROOT / "aos/aos/doctype/aos_review/aos_review.json").read_text())
        status = next(field for field in schema["fields"] if field.get("fieldname") == "status")
        self.assertEqual(status["options"].splitlines(), ["Pending", "Approved", "Rejected", "Hidden", "Withdrawn"])
        fields = {field.get("fieldname") for field in schema["fields"]}
        self.assertTrue({"review_key", "eligibility_basis", "moderation_generation", "withdrawn_on"} <= fields)

    def test_every_review_endpoint_has_a_reviewed_rate_limit_policy(self):
        registry = json.loads((ROOT / "ci/public-endpoint-rate-limits.json").read_text())
        endpoints = {row["endpoint"] for row in registry}
        tree = ast.parse((ROOT / "aos/api/v1/reviews/__init__.py").read_text())
        functions = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "whitelist"
                for decorator in node.decorator_list
            )
        }
        expected = {f"aos.api.v1.reviews.__init__.{name}" for name in functions}
        self.assertTrue(expected <= endpoints)
    def test_permanent_deletion_retains_reviews_but_removes_private_actions(self):
        source = (ROOT / "aos/services/account_deletion_service.py").read_text()
        self.assertIn("def _cleanup_review_account_data", source)
        self.assertIn("reviews_retained_anonymized", source)
        self.assertIn('where_sql="user = %s"', source)
        self.assertIn('where_sql="reported_by = %s"', source)

    def test_public_and_self_serializers_do_not_expose_moderator_notes(self):
        source = (ROOT / "aos/services/reviews/serializers.py").read_text()
        self.assertNotIn('"review_notes":', source)
        self.assertIn('"moderation_reason_code"', source)

    def test_reaction_counts_have_one_canonical_aggregate_helper(self):
        aggregate_source = (ROOT / "aos/services/reviews/aggregates.py").read_text()
        controller_source = (
            ROOT / "aos/aos/doctype/aos_review_reaction/aos_review_reaction.py"
        ).read_text()
        service_source = (ROOT / "aos/services/reviews/service.py").read_text()
        self.assertIn("def recompute_review_reaction_counts", aggregate_source)
        self.assertIn("recompute_review_reaction_counts", controller_source)
        self.assertIn("recompute_review_reaction_counts", service_source)
    def test_existing_v1_review_contracts_keep_legacy_codes_and_flat_create_fields(self):
        wrapper_source = (ROOT / "aos/api/v1/reviews/__init__.py").read_text()
        service_source = (ROOT / "aos/services/reviews/service.py").read_text()
        self.assertIn('"ALREADY_REVIEWED"', wrapper_source)
        self.assertIn('"canonical_error"', wrapper_source)
        self.assertIn('"id": review.name', service_source)
        self.assertIn('"moderation_job_status"', service_source)
    def test_historical_unique_patch_no_longer_deletes_duplicate_reviews(self):
        source = (ROOT / "aos/patches/v1_0/add_unique_constraints.py").read_text()
        tree = ast.parse(source)
        execute = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "execute"
        )
        execute_source = ast.get_source_segment(source, execute) or ""
        self.assertNotIn("_dedupe_reviews()", execute_source)
        self.assertNotIn('"constraint_name": "unique_aos_review_ad_reviewer"', source)
    def test_reviews_migration_processes_duplicate_and_backfill_work_in_batches(self):
        source = (ROOT / "aos/patches/v1_0/harden_reviews_subsystem.py").read_text()
        self.assertIn("_BATCH_SIZE = 250", source)
        self.assertGreaterEqual(source.count("LIMIT %s"), 5)
        self.assertIn("def _backfill_ad_aggregates", source)
        self.assertIn("def _backfill_seller_aggregates", source)
        self.assertIn("status != 'Withdrawn'", source)
    def test_review_reports_reuse_central_report_reasons(self):
        schema = json.loads(
            (ROOT / "aos/aos/doctype/aos_review_report/aos_review_report.json").read_text()
        )
        reason = next(field for field in schema["fields"] if field.get("fieldname") == "reason")
        self.assertEqual(reason.get("fieldtype"), "Link")
        self.assertEqual(reason.get("options"), "AOS Report Reason")
        service = (ROOT / "aos/services/reviews/service.py").read_text()
        self.assertIn('frappe.db.exists("AOS Report Reason"', service)
    def test_review_notifications_use_canonical_delivery_service(self):
        notification_source = (ROOT / "aos/services/notification_service.py").read_text()
        moderation_source = (ROOT / "aos/services/moderation_service.py").read_text()
        categories = (ROOT / "aos/api/notifications/constants.py").read_text()
        for notification_type in ("review_received", "review_approved", "review_rejected"):
            self.assertIn(notification_type, notification_source)
            self.assertIn(notification_type, categories)
        self.assertIn("_notify_review_moderation_result", moderation_source)
        self.assertIn("NotificationService.notify_review_received", moderation_source)
    def test_review_aggregates_are_locked_and_reconcilable_for_ads_and_sellers(self):
        controller = (ROOT / "aos/aos/doctype/aos_review/aos_review.py").read_text()
        aggregates = (ROOT / "aos/services/reviews/aggregates.py").read_text()
        self.assertIn("lock_target=True", controller)
        self.assertIn("def reconcile_review_aggregates", aggregates)
        self.assertIn("def reconcile_seller_review_aggregates", aggregates)
        self.assertNotIn("frappe.db.commit", aggregates)
    def test_owned_review_lock_fails_closed_before_loading_document(self):
        source = (ROOT / "aos/services/reviews/service.py").read_text()
        self.assertIn("SELECT name, reviewer FROM `tabAOS Review`", source)
        self.assertIn('raise ReviewNotFoundError("Review not found.")', source)

    def test_legacy_review_media_helpers_delegate_to_central_policy(self):
        source = (ROOT / "aos/api/reviews/media.py").read_text()
        self.assertIn("from aos.services.reviews.validation import normalize_images", source)
        self.assertNotIn("Maximum 5 images allowed", source)

