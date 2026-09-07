from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class TestReportProductionSourceGuards(unittest.TestCase):
    def test_public_report_surface_preserves_only_existing_targets(self):
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
        self.assertEqual(functions, {"list_report_reasons", "report_ad", "report_user", "report_short"})
        for unsupported in ("report_live", "report_message", "report_call", "report_comment", "report_seller"):
            self.assertNotIn(unsupported, source)

    def test_v1_report_wrappers_strip_only_framework_transport_metadata(self):
        source = _source("aos/api/v1/reports/__init__.py")
        self.assertEqual(source.count("client_kwargs(kwargs)"), 4)
        self.assertNotIn("frappe.db", source)

    def test_all_report_creation_endpoints_are_private_rate_limited_and_savepoint_scoped(self):
        for relative in (
            "aos/api/reports/report_user.py",
            "aos/api/reports/report_ad.py",
            "aos/api/reports/report_short.py",
        ):
            source = _source(relative)
            self.assertIn("require_login()", source, relative)
            self.assertIn("set_private_no_store()", source, relative)
            self.assertIn("rate_limit_key", source, relative)
            self.assertIn("frappe.db.savepoint(savepoint)", source, relative)
            self.assertIn("frappe.db.rollback(save_point=savepoint)", source, relative)
            self.assertNotIn("frappe.db.commit", source, relative)

    def test_review_report_boundary_is_savepoint_scoped_without_full_transaction_rollback(self):
        source = _source("aos/api/reviews/report.py")
        self.assertIn("frappe.db.savepoint(savepoint)", source)
        self.assertIn("frappe.db.rollback(save_point=savepoint)", source)
        self.assertNotIn("frappe.db.commit", source)
        self.assertNotIn("frappe.db.rollback()", source)

    def test_report_service_never_commits_outer_transactions(self):
        offenders = []
        for path in (ROOT / "aos/services/reports").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "frappe.db.commit(" in source or "frappe.db.rollback()" in source:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_report_reasons_match_authenticated_registry_and_have_explicit_limit(self):
        source = _source("aos/api/reports/reasons.py")
        self.assertIn("require_login()", source)
        self.assertIn("REPORT_REASONS_LIMIT_PER_MINUTE_PER_USER", source)
        self.assertIn("set_private_no_store()", source)
        registry = json.loads(_source("ci/public-endpoint-rate-limits.json"))
        row = next(item for item in registry if item["endpoint"] == "aos.api.v1.reports.__init__.list_report_reasons")
        self.assertEqual(row["access"], "authenticated")

    def test_request_validation_is_strict_bounded_and_alias_conflict_aware(self):
        validation = _source("aos/services/reports/validation.py")
        service = _source("aos/services/reports/service.py")
        self.assertIn("Unsupported report fields", validation)
        self.assertIn('TRANSPORT_FIELDS = frozenset({"cmd"})', _source("aos/services/reports/constants.py"))
        self.assertIn("Conflicting", validation)
        self.assertIn("normalize_optional_bool", validation)
        self.assertIn("max_length=1000", service)
        self.assertIn("max_length=2000", service)

    def test_user_report_uses_public_account_resolution_and_post_lock_revalidation(self):
        service = _source("aos/services/reports/service.py")
        self.assertIn("resolve_account_reference", service)
        self.assertIn("SELECT name FROM `tabAOS Profile`", service)
        block = service.split("def report_user", 1)[1].split("def report_short", 1)[0]
        self.assertGreaterEqual(block.count("require_reportable_user"), 2)
        self.assertNotIn('doc.reported_by = request.get(', block)

    def test_duplicate_integrity_is_database_backed_for_every_report_type(self):
        indexes = _source("aos/patches/v1_0/install_report_indexes.py")
        user_schema = json.loads(_source("aos/aos/doctype/aos_user_report/aos_user_report.json"))
        self.assertIn("active_key", {row.get("fieldname") for row in user_schema["fields"]})
        self.assertIn("uq_aos_user_report_active", indexes)
        self.assertIn("uq_aos_ad_report_user_ad", _source("aos/patches/v1_0/harden_ads_subsystem.py"))
        self.assertIn("uq_short_report_active", _source("aos/patches/v1_0/install_shorts_indexes.py"))
        self.assertIn("uq_aos_review_report_user", _source("aos/patches/v1_0/harden_reviews_subsystem.py"))

    def test_report_statuses_and_actions_are_not_invented(self):
        expected = {
            "aos_user_report": (["Reviewing", "Resolved", "Rejected"], ["", "Warn User", "Suspend User", "Dismiss Report"]),
            "aos_ad_report": (["Reviewing", "Resolved", "Rejected"], ["", "Warn Seller", "Suspended Ad", "Suspended Seller"]),
            "aos_short_report": (["Reviewing", "Resolved", "Rejected"], ["", "Hide Short", "Warn Creator", "Suspend Creator", "Dismiss Report"]),
            "aos_review_report": (["Reviewing", "Resolved", "Rejected"], None),
        }
        for name, (statuses, actions) in expected.items():
            schema = json.loads(_source(f"aos/aos/doctype/{name}/{name}.json"))
            fields = {row["fieldname"]: row for row in schema["fields"]}
            self.assertEqual(fields["status"]["options"].splitlines(), statuses)
            if actions is None:
                self.assertNotIn("admin_action", fields)
            else:
                self.assertEqual(fields["admin_action"]["options"].splitlines(), actions)

    def test_human_review_transitions_are_centralized_and_terminal(self):
        lifecycle = _source("aos/services/reports/lifecycle.py")
        constants = _source("aos/services/reports/constants.py")
        self.assertIn("REPORT_TRANSITIONS", lifecycle)
        self.assertIn('STATUS_RESOLVED: frozenset()', constants)
        self.assertIn('STATUS_REJECTED: frozenset()', constants)
        self.assertIn("require_reviewer(actor, doctype=doc.doctype)", lifecycle)
        self.assertIn("A completed moderation action cannot be changed", lifecycle)

    def test_submitted_report_evidence_is_immutable_during_review(self):
        lifecycle = _source("aos/services/reports/lifecycle.py")
        self.assertIn("SUBMISSION_FIELDS", lifecycle)
        self.assertIn("Submitted report details cannot be edited during review", lifecycle)
        for name in ("aos_user_report", "aos_ad_report", "aos_short_report", "aos_review_report"):
            controller = _source(f"aos/aos/doctype/{name}/{name}.py")
            self.assertIn("validate_report_lifecycle", controller)
            self.assertIn("stamp_review_metadata", controller)

    def test_resolved_admin_actions_are_one_shot_and_reuse_domain_services(self):
        source = _source("aos/services/reports/moderation.py")
        self.assertIn("apply_admin_action_once", source)
        self.assertIn("set_seller_status", source)
        self.assertIn("validate_status_transition", source)
        self.assertIn("revoke_account_access", source)
        self.assertIn("enqueue_short_search_index", source)
        self.assertNotIn("NotificationService", source)

    def test_report_logs_do_not_include_reporter_or_user_identifiers(self):
        source = _source("aos/services/reports/observability.py")
        for forbidden in ("reported_by", "reported_user", "email", "phone", "details"):
            self.assertNotIn(forbidden, source)
        self.assertIn("report_id", source)
        self.assertIn("status", source)

    def test_reason_deactivation_does_not_block_reviewing_existing_reports(self):
        for name in ("aos_user_report", "aos_ad_report", "aos_short_report", "aos_review_report"):
            source = _source(f"aos/aos/doctype/{name}/{name}.py")
            self.assertIn("if previous is None", source)
            self.assertIn("validate_active_reason", source)
            self.assertIn('frappe.db.exists("AOS Report Reason", self.reason)', source)

    def test_sensitive_report_records_are_not_renamable_or_deletable_from_desk(self):
        for name in ("aos_user_report", "aos_ad_report", "aos_short_report", "aos_review_report"):
            schema = json.loads(_source(f"aos/aos/doctype/{name}/{name}.json"))
            self.assertFalse(bool(schema.get("allow_rename")), name)
            self.assertTrue(bool(schema.get("track_changes")), name)
            self.assertFalse(bool(schema.get("index_web_pages_for_search")), name)
            system_manager = next(row for row in schema["permissions"] if row.get("role") == "System Manager")
            self.assertFalse(bool(system_manager.get("delete")), name)
            self.assertFalse(bool(system_manager.get("share")), name)


    def test_report_reason_master_is_auditable_and_deactivated_instead_of_renamed_or_deleted(self):
        schema = json.loads(_source("aos/aos/doctype/aos_report_reason/aos_report_reason.json"))
        self.assertFalse(bool(schema.get("allow_rename")))
        self.assertTrue(bool(schema.get("track_changes")))
        system_manager = next(row for row in schema["permissions"] if row.get("role") == "System Manager")
        self.assertFalse(bool(system_manager.get("delete")))
        controller = _source("aos/aos/doctype/aos_report_reason/aos_report_reason.py")
        self.assertIn("140", controller)
        self.assertIn("80", controller)
        self.assertIn("titles cannot be changed after creation", controller)

    def test_desk_review_uses_authoritative_row_locks_for_concurrent_reviewers(self):
        repository = _source("aos/services/reports/repository.py")
        self.assertIn("FOR UPDATE", repository)
        self.assertIn("_REPORT_TABLES", repository)
        for name in ("aos_user_report", "aos_ad_report", "aos_short_report", "aos_review_report"):
            controller = _source(f"aos/aos/doctype/{name}/{name}.py")
            self.assertIn("locked_previous_report", controller)

    def test_review_reporting_locks_target_before_visibility_and_duplicate_checks(self):
        service = _source("aos/services/reviews/service.py")
        block = service.split("def report(self", 1)[1].split("@staticmethod", 1)[0]
        self.assertIn("`tabAOS Review`", block)
        self.assertIn("FOR UPDATE", block)
        self.assertLess(block.index("FOR UPDATE"), block.index("review.status != STATUS_APPROVED"))

    def test_reporter_owned_private_rows_are_removed_only_after_restore_window(self):
        source = _source("aos/services/account_deletion_service.py")
        self.assertIn("def _cleanup_report_account_data", source)
        self.assertIn('"user_reports_removed"', source)
        self.assertIn('"short_reports_removed"', source)
        self.assertIn('"ad_reports_removed"', source)
        self.assertIn("ad_report_totals_recalculated", source)

    def test_report_migration_is_registered_batched_and_commit_free(self):
        patches = _source("aos/patches.txt")
        self.assertIn("aos.patches.v1_0.harden_reports_subsystem", patches)
        source = _source("aos/patches/v1_0/harden_reports_subsystem.py")
        self.assertIn("_BATCH_SIZE = 250", source)
        self.assertIn("LIMIT %s", source)
        self.assertNotIn("frappe.db.commit", source)
        self.assertNotIn("frappe.db.add_index", source)
        self.assertNotIn("frappe.db.add_unique", source)
        self.assertNotIn("frappe.reload_doc", source)
        self.assertIn("status = 'Rejected'", source)

    def test_report_index_repair_runs_after_shared_hardening_and_is_schema_only(self):
        patches = _source("aos/patches.txt")
        harden = "aos.patches.v1_0.harden_reports_subsystem"
        repair = "aos.patches.v1_0.install_report_indexes"
        self.assertIn(repair, patches)
        self.assertLess(patches.index(harden), patches.index(repair))

        harden_source = _source("aos/patches/v1_0/harden_reports_subsystem.py")
        self.assertNotIn("frappe.reload_doc", harden_source)

        source = _source("aos/patches/v1_0/install_report_indexes.py")
        for index_name in (
            "uq_short_report_active",
            "idx_short_report_review",
            "uq_aos_ad_report_user_ad",
            "uq_aos_review_report_user",
        ):
            self.assertIn(index_name, source)
        self.assertNotIn("frappe.db.commit", source)
        self.assertNotIn("frappe.db.rollback", source)
        tree = ast.parse(source)
        forbidden = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in {"set_value", "insert", "save", "delete"}:
                forbidden.append(f"{node.func.attr}@{node.lineno}")
            if node.func.attr == "sql" and node.args:
                query = node.args[0]
                literal = ""
                if isinstance(query, ast.Constant) and isinstance(query.value, str):
                    literal = query.value
                elif isinstance(query, ast.JoinedStr):
                    literal = "".join(
                        value.value
                        for value in query.values
                        if isinstance(value, ast.Constant) and isinstance(value.value, str)
                    )
                if literal.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "TRUNCATE")):
                    forbidden.append(f"sql-dml@{node.lineno}")
        self.assertEqual(forbidden, [])

    def test_rate_limit_registry_covers_every_public_report_endpoint(self):
        registry = json.loads(_source("ci/public-endpoint-rate-limits.json"))
        endpoints = {row["endpoint"] for row in registry}
        tree = ast.parse(_source("aos/api/v1/reports/__init__.py"))
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
        expected = {f"aos.api.v1.reports.__init__.{name}" for name in functions}
        self.assertTrue(expected <= endpoints)
        self.assertIn("aos.api.v1.reviews.__init__.report_review", endpoints)


if __name__ == "__main__":
    unittest.main()
