from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


class TestReviewsContracts(unittest.TestCase):
    def test_v1_exposes_the_canonical_review_contract(self):
        source = (ROOT / "aos/api/v1/reviews/__init__.py").read_text()
        tree = ast.parse(source)
        exported = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertEqual(
            exported,
            {
                "create_review",
                "update_review",
                "delete_review",
                "get_review",
                "list_reviews",
                "list_my_reviews",
                "list_reviews_received",
                "get_review_viewer_state",
                "like_review",
                "unlike_review",
                "dislike_review",
                "undislike_review",
                "report_review",
            },
        )
        self.assertIn("client_kwargs(kwargs)", source)

    def test_service_declares_strict_current_fields_and_cursor_pagination(self):
        source = (ROOT / "aos/services/reviews/service.py").read_text()
        self.assertIn('CREATE_FIELDS = frozenset({"ad_id", "rating", "title", "comment", "media"})', source)
        self.assertIn('UPDATE_FIELDS = frozenset({"review_id", "version", "rating", "title", "comment", "media"})', source)
        self.assertIn('LIST_FIELDS = frozenset({"ad_id", "sort", "rating", "with_media", "limit", "cursor"})', source)

    def test_review_schema_uses_scalable_names_and_public_id(self):
        review = json.loads((ROOT / "aos/aos/doctype/aos_review/aos_review.json").read_text())
        reaction = json.loads((ROOT / "aos/aos/doctype/aos_review_reaction/aos_review_reaction.json").read_text())
        report = json.loads((ROOT / "aos/aos/doctype/aos_review_report/aos_review_report.json").read_text())
        self.assertNotIn("naming_series:", str(review.get("autoname", "")))
        self.assertFalse(review.get("autoname"))
        self.assertFalse(reaction.get("autoname"))
        self.assertFalse(report.get("autoname"))
        public_id = next(field for field in review["fields"] if field.get("fieldname") == "public_id")
        rating = next(field for field in review["fields"] if field.get("fieldname") == "rating")
        self.assertEqual(rating.get("fieldtype"), "Int")
        self.assertEqual(public_id.get("unique"), 1)
        self.assertEqual(public_id.get("read_only"), 1)
        review_perm = review["permissions"][0]
        reaction_perm = reaction["permissions"][0]
        self.assertEqual((review_perm.get("create"), review_perm.get("delete"), review_perm.get("write")), (0, 0, 1))
        self.assertEqual((reaction_perm.get("create"), reaction_perm.get("delete"), reaction_perm.get("write")), (0, 0, 0))

    def test_schema_invariants_are_installed_by_the_current_schema_installer(self):
        patches = (ROOT / "aos/patches.txt").read_text()
        self.assertIn("aos.patches.v1_0.install_review_indexes", patches)
        installer = (ROOT / "aos/patches/v1_0/install_review_indexes.py").read_text()
        self.assertIn("uq_aos_review_key", installer)
        self.assertIn("uq_aos_review_reaction_user", installer)
        self.assertNotIn("frappe.db.commit", installer)

    def test_manual_moderation_is_desk_owned_and_permission_protected(self):
        js = (ROOT / "aos/aos/doctype/aos_review/aos_review.js").read_text()
        transport = (ROOT / "aos/aos/doctype/aos_review/review.py").read_text()
        service = (ROOT / "aos/services/reviews/moderation.py").read_text()
        self.assertIn("Approve Review", js)
        self.assertIn("Reject Review", js)
        self.assertIn("has_doctype_permission", service)
        self.assertIn("REVIEW_VERSION_CONFLICT", service)
        self.assertIn('@frappe.whitelist(methods=["POST"])', transport)

    def test_media_and_serializer_use_hardened_batched_contract(self):
        source = (ROOT / "aos/services/reviews/serializers.py").read_text()
        self.assertIn("get_public_attachment_url_map", source)
        self.assertNotIn("get_public_url(media_id)", source)
        schema = json.loads((ROOT / "aos/aos/doctype/aos_review_image/aos_review_image.json").read_text())
        fields = {field.get("fieldname") for field in schema.get("fields", [])}
        self.assertEqual(fields, {"media"})

    def test_review_controller_does_not_shadow_frappe_internal_action_state(self):
        controller = (ROOT / "aos/aos/doctype/aos_review/aos_review.py").read_text()
        self.assertIn("def _review_action(self)", controller)
        self.assertNotIn("def _action(self)", controller)

    def test_reaction_relationship_and_aggregate_updates_are_atomic(self):
        controller = (ROOT / "aos/aos/doctype/aos_review_reaction/aos_review_reaction.py").read_text()
        aggregate = (ROOT / "aos/services/reviews/aggregates.py").read_text()
        self.assertIn("review_reaction_name", controller)
        self.assertIn("apply_reaction_count_delta", controller)
        self.assertIn("GREATEST(COALESCE(like_count, 0)", aggregate)
        self.assertIn("review_rating_sum", aggregate)
        self.assertNotIn("frappe.db.commit", aggregate)

    def test_every_review_public_endpoint_has_rate_limit_registry_coverage(self):
        registry = json.loads((ROOT / "ci/public-endpoint-rate-limits.json").read_text())
        endpoints = {row["endpoint"] for row in registry}
        tree = ast.parse((ROOT / "aos/api/v1/reviews/__init__.py").read_text())
        functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
            and any(isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "whitelist" for dec in node.decorator_list)
        }
        expected = {f"aos.api.v1.reviews.__init__.{name}" for name in functions}
        self.assertTrue(expected <= endpoints)

    def test_account_deletion_retains_authored_reviews_and_removes_private_actions(self):
        source = (ROOT / "aos/services/account_deletion_service.py").read_text()
        self.assertIn("reviews_retained_anonymized", source)
        self.assertIn('"AOS Review Reaction"', source)
        self.assertIn('"AOS Review Report"', source)

    def test_race_paths_have_explicit_duplicate_and_opposing_reaction_coverage(self):
        concurrency = (ROOT / "aos/api/reviews/tests/test_reviews_concurrency.py").read_text()
        for invariant in (
            "test_duplicate_review_insert_race_returns_database_winner",
            "test_concurrent_same_reaction_insert_converges_to_database_winner",
            "test_concurrent_opposing_reaction_insert_switches_database_winner",
        ):
            self.assertIn(invariant, concurrency)

    def test_review_readme_is_the_only_feature_markdown(self):
        docs = sorted(path.name for path in (ROOT / "docs/features/reviews").glob("*.md"))
        self.assertEqual(docs, ["README.md"])
