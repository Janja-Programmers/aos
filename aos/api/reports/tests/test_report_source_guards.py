from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _schema(name: str) -> dict:
    return json.loads(_source(f"aos/aos/doctype/{name}/{name}.json"))


class TestReportProductionSourceGuards(unittest.TestCase):
    def test_public_reports_surface_is_exactly_user_ad_short_and_scoped_reasons(self):
        source = _source("aos/api/v1/reports/__init__.py")
        tree = ast.parse(source)
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
        self.assertEqual(functions, {"get_report_reasons", "report_user", "report_ad", "report_short"})
        self.assertNotIn("list_report_reasons", source)
        for unsupported in ("report_live", "report_message", "report_call", "report_comment", "report_seller"):
            self.assertNotIn(unsupported, source)

    def test_public_requests_have_one_canonical_field_set_and_no_aliases(self):
        constants = _source("aos/services/reports/constants.py")
        self.assertIn('USER_REPORT_FIELDS = frozenset({"account_id", "reason_id", "details"})', constants)
        self.assertIn('AD_REPORT_FIELDS = frozenset({"ad_id", "reason_id", "details"})', constants)
        self.assertIn('SHORT_REPORT_FIELDS = frozenset({"short_id", "reason_id", "details"})', constants)
        self.assertIn('REASONS_FIELDS = frozenset({"target_type"})', constants)
        validation = _source("aos/services/reports/validation.py")
        self.assertIn("Unsupported report fields", validation)
        for stale in ("target_user", "also_block", "block_user", "normalize_optional_bool"):
            self.assertNotIn(stale, constants + validation)

    def test_v1_wrappers_strip_only_framework_transport_metadata(self):
        source = _source("aos/api/v1/reports/__init__.py")
        self.assertEqual(source.count("client_kwargs(kwargs)"), 4)
        self.assertNotIn("frappe.db", source)

    def test_write_endpoints_are_private_rate_limited_and_savepoint_scoped(self):
        for relative in (
            "aos/api/reports/report_user.py",
            "aos/api/reports/report_ad.py",
            "aos/api/reports/report_short.py",
        ):
            source = _source(relative)
            self.assertIn("require_login()", source, relative)
            self.assertIn("set_private_no_store()", source, relative)
            self.assertIn("limit_report_submission(", source, relative)
            self.assertIn("frappe.db.savepoint(savepoint)", source, relative)
            self.assertIn("frappe.db.rollback(save_point=savepoint)", source, relative)
            self.assertNotIn("frappe.db.commit", source, relative)

    def test_reason_endpoint_is_authenticated_scoped_deterministic_and_public_safe(self):
        source = _source("aos/api/reports/reasons.py")
        self.assertIn("require_login()", source)
        self.assertIn("REPORT_REASONS_LIMIT_PER_MINUTE_PER_USER", source)
        self.assertIn("t.target_type = %s", source)
        self.assertIn("r.is_enabled = 1", source)
        self.assertIn("ORDER BY r.sort_order ASC, r.label ASC, r.name ASC", source)
        self.assertIn("safe_fail_from_exception", source)
        self.assertNotIn("fail(str(exc)", source)
        for leaked in ("owner", "modified_by", "creation", "modified"):
            self.assertNotIn(f'"{leaked}":', source)
        registry = json.loads(_source("ci/public-endpoint-rate-limits.json"))
        endpoints = {item["endpoint"] for item in registry}
        self.assertIn("aos.api.v1.reports.__init__.get_report_reasons", endpoints)
        self.assertNotIn("aos.api.v1.reports.__init__.list_report_reasons", endpoints)

    def test_reason_master_uses_stable_id_and_normalized_target_scope(self):
        schema = _schema("aos_report_reason")
        fields = {row["fieldname"]: row for row in schema["fields"]}
        self.assertEqual(schema["autoname"], "field:reason_id")
        self.assertTrue(fields["reason_id"].get("unique"))
        self.assertEqual(fields["allowed_targets"]["options"], "AOS Report Reason Target")
        self.assertIn("is_enabled", fields)
        self.assertNotIn("title", fields)
        self.assertNotIn("is_active", fields)
        child = _schema("aos_report_reason_target")
        child_fields = {row["fieldname"]: row for row in child["fields"]}
        self.assertEqual(child_fields["target_type"]["options"].splitlines(), ["User", "Ad", "Short", "Review"])

    def test_seed_catalog_is_canonical_and_shared_reasons_are_not_duplicated(self):
        source = _source("aos/services/reports/catalog.py")
        tree = ast.parse(source)
        self.assertIn('"reason_id": "scam_fraud"', source)
        self.assertEqual(source.count('"reason_id": "scam_fraud"'), 1)
        self.assertIn("REPORT_TARGET_USER, REPORT_TARGET_AD, REPORT_TARGET_SHORT", source)
        self.assertIn('"reason_id": "other"', source)
        self.assertIsNotNone(tree)

    def test_target_adapters_reuse_hardened_feature_contracts(self):
        source = _source("aos/services/reports/service.py")
        self.assertIn("normalize_public_account_id", source)
        self.assertIn("resolve_account_reference", source)
        self.assertIn("require_public_ad_for_viewer", source)
        self.assertIn("normalize_short_id", source)
        self.assertIn("can_view_short", source)
        self.assertIn("visible.public_id", source)
        self.assertNotIn("record_report_user_activity", source)
        self.assertNotIn("record_ad_report_activity", source)
        self.assertNotIn("record_short_report_activity", source)

    def test_report_service_has_no_moderation_or_notification_side_effects(self):
        reports_dir = ROOT / "aos/services/reports"
        self.assertFalse((reports_dir / "moderation.py").exists())
        joined = "\n".join(path.read_text(encoding="utf-8") for path in reports_dir.rglob("*.py"))
        for forbidden in (
            "NotificationService",
            "enqueue_discovery_refresh",
            "set_seller_status",
            "revoke_account_access",
            "enqueue_short_search_index",
        ):
            self.assertNotIn(forbidden, joined)

    def test_duplicate_integrity_is_reviewing_only_and_database_backed_for_three_targets(self):
        indexes = _source("aos/patches/v1_0/install_report_indexes.py")
        for schema_name, index_name in (
            ("aos_user_report", "uq_aos_user_report_active"),
            ("aos_ad_report", "uq_aos_ad_report_active"),
            ("aos_short_report", "uq_short_report_active"),
        ):
            fields = {row["fieldname"] for row in _schema(schema_name)["fields"]}
            self.assertIn("active_key", fields)
            self.assertIn(index_name, indexes)
        self.assertIn('("AOS Ad Report", "uq_aos_ad_report_user_ad")', indexes)
        repository = _source("aos/services/reports/repository.py")
        self.assertIn('params: list[str] = [key, STATUS_REVIEWING]', repository)
        self.assertIn('WHERE active_key = %s', repository)
        service = _source("aos/services/reports/service.py")
        self.assertIn("is_duplicate_entry_error", service)
        self.assertIn("idempotent_replay=True", service)

    def test_unique_keys_and_locking_race_reconciliation_cover_concurrent_duplicates(self):
        service = _source("aos/services/reports/service.py")
        repository = _source("aos/services/reports/repository.py")
        self.assertIn("is_duplicate_entry_error", service)
        self.assertIn("lock=True", service)
        self.assertIn("FOR UPDATE", repository)
        self.assertIn("unique ``active_key``", repository)
        # Do not serialize every reporter on one viral target row.
        self.assertNotIn("FOR UPDATE", service)
        controllers = "\n".join(
            _source(f"aos/aos/doctype/{name}/{name}.py")
            for name in ("aos_user_report", "aos_ad_report", "aos_short_report")
        )
        self.assertEqual(controllers.count("active_report_key("), 3)
        self.assertEqual(controllers.count("find_reviewing_report("), 3)

    def test_report_ids_are_distributed_safe_opaque_ids(self):
        expected = {
            "aos_user_report": "URPT",
            "aos_ad_report": "ARPT",
            "aos_short_report": "SRPT",
        }
        for name, prefix in expected.items():
            source = _source(f"aos/aos/doctype/{name}/{name}.py")
            schema = _schema(name)
            self.assertIn(f'new_prefixed_name("{prefix}")', source)
            self.assertNotIn("naming_series", {row["fieldname"] for row in schema["fields"]})

    def test_lifecycle_is_one_canonical_terminal_workflow_and_staff_only(self):
        constants = _source("aos/services/reports/constants.py")
        self.assertIn('STATUS_REVIEWING = "Reviewing"', constants)
        self.assertIn('STATUS_RESOLVED = "Resolved"', constants)
        self.assertIn('STATUS_REJECTED = "Rejected"', constants)
        self.assertIn("STATUS_RESOLVED: frozenset()", constants)
        self.assertIn("STATUS_REJECTED: frozenset()", constants)
        lifecycle = _source("aos/services/reports/lifecycle.py")
        self.assertIn("require_reviewer(actor, doctype=doc.doctype)", lifecycle)
        self.assertIn("Submitted report details cannot be edited during review", lifecycle)
        for name in ("aos_user_report", "aos_ad_report", "aos_short_report"):
            schema = _schema(name)
            fields = {row["fieldname"]: row for row in schema["fields"]}
            self.assertEqual(fields["status"]["options"].splitlines(), ["Reviewing", "Resolved", "Rejected"])
            self.assertNotIn("admin_action", fields)
            role = next(row for row in schema["permissions"] if row.get("role") == "System Manager")
            self.assertFalse(bool(role.get("create")))
            self.assertTrue(bool(role.get("write")))

    def test_submitted_evidence_and_reporter_are_read_only_in_desk(self):
        for name, evidence in (
            ("aos_user_report", ("reported_user", "reported_by", "reason", "details")),
            ("aos_ad_report", ("ad", "seller", "reported_by", "reason", "details")),
            ("aos_short_report", ("short", "short_owner", "reported_by", "reason", "details")),
        ):
            fields = {row["fieldname"]: row for row in _schema(name)["fields"]}
            for field in evidence:
                self.assertTrue(bool(fields[field].get("read_only")), f"{name}.{field}")

    def test_details_are_bounded_and_html_and_control_characters_are_rejected(self):
        validation = _source("aos/services/reports/validation.py")
        constants = _source("aos/services/reports/constants.py")
        self.assertIn("strip_html(text) != text", validation)
        self.assertIn('unicodedata.category(char) == "Cc"', validation)
        self.assertIn('"AOS User Report": 1000', constants)
        self.assertIn('"AOS Ad Report": 1000', constants)
        self.assertIn('"AOS Short Report": 1000', constants)

    def test_public_submission_projection_does_not_expose_private_or_internal_fields(self):
        service = _source("aos/services/reports/service.py")
        projection = service.split("def _projection", 1)[1]
        for forbidden in (
            '"reported_by"',
            '"reported_user"',
            '"seller"',
            '"short_owner"',
            '"reviewed_by"',
            '"reviewed_on"',
            '"owner"',
            '"modified_by"',
        ):
            self.assertNotIn(forbidden, projection)
        for required in (
            '"report_id"',
            '"report_type"',
            '"target_id"',
            '"reason_id"',
            '"status"',
            '"created_at"',
            '"idempotent_replay"',
        ):
            self.assertIn(required, projection)

    def test_reporter_identity_is_session_owned(self):
        for name in ("aos_user_report", "aos_ad_report", "aos_short_report"):
            source = _source(f"aos/aos/doctype/{name}/{name}.py")
            self.assertIn("frappe.session", source)
            self.assertIn("self.reported_by =", source)
        service = _source("aos/services/reports/service.py")
        self.assertNotIn('request.get("reported_by")', service)

    def test_rate_limits_are_distributed_and_separate_for_user_and_target(self):
        source = _source("aos/api/reports/rate_limits.py")
        self.assertIn('rate_limit_key("reports", report_type, "user", user)', source)
        self.assertIn('rate_limit_key("reports", report_type, "target", user, target_id)', source)
        self.assertNotIn("dict[", source)
        reasons = _source("aos/api/reports/reasons.py")
        self.assertIn('rate_limit_key("reports", "reasons", "user", current_user)', reasons)

    def test_migration_reconciles_data_before_final_indexes_and_drops_old_ad_uniqueness(self):
        patches = _source("aos/patches.txt")
        finalize = "aos.patches.v1_0.finalize_reports_domain"
        indexes = "aos.patches.v1_0.install_report_indexes"
        self.assertIn(finalize, patches)
        self.assertNotIn("aos.patches.v1_0.harden_reports_subsystem", patches)
        self.assertLess(patches.index(finalize), patches.index(indexes))
        source = _source("aos/patches/v1_0/finalize_reports_domain.py")
        self.assertIn("_canonicalize_known_reason_links", source)
        self.assertIn("_reconcile_reviewing_duplicates", source)
        self.assertIn("_backfill_active_keys", source)
        index_source = _source("aos/patches/v1_0/install_report_indexes.py")
        self.assertIn("DROP INDEX", index_source)
        self.assertIn("uq_aos_ad_report_user_ad", index_source)

    def test_review_reporting_consumes_classified_reason_master_without_becoming_public_reports_target(self):
        controller = _source("aos/aos/doctype/aos_review_report/aos_review_report.py")
        service = _source("aos/services/reviews/service.py")
        self.assertIn("REPORT_TARGET_REVIEW", controller)
        self.assertIn("validate_reason_for_target", controller)
        self.assertIn("REPORT_TARGET_REVIEW", service)
        self.assertIn("validate_reason_for_target", service)
        public = _source("aos/services/reports/constants.py")
        self.assertIn('"user": REPORT_TARGET_USER', public)
        self.assertIn('"ad": REPORT_TARGET_AD', public)
        self.assertIn('"short": REPORT_TARGET_SHORT', public)
        self.assertNotIn('"review": REPORT_TARGET_REVIEW', public.split("PUBLIC_REPORT_TARGETS", 1)[1].split("}", 1)[0])

    def test_reports_do_not_commit_outer_transaction(self):
        offenders = []
        for path in (ROOT / "aos/services/reports").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "frappe.db.commit(" in source or "frappe.db.rollback()" in source:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_sensitive_report_records_are_not_renamable_deletable_shared_or_web_indexed(self):
        for name in ("aos_user_report", "aos_ad_report", "aos_short_report", "aos_review_report"):
            schema = _schema(name)
            self.assertFalse(bool(schema.get("allow_rename")), name)
            self.assertTrue(bool(schema.get("track_changes")), name)
            self.assertFalse(bool(schema.get("index_web_pages_for_search")), name)
            role = next(row for row in schema["permissions"] if row.get("role") == "System Manager")
            self.assertFalse(bool(role.get("delete")), name)
            self.assertFalse(bool(role.get("share")), name)


if __name__ == "__main__":
    unittest.main()
