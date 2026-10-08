from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.utils import migration_preflight


class TestMigrationPreflight(FrappeTestCase):
    def _run(self, *, environment: str, backup_ready: bool = True):
        db = frappe.db

        def sql(query, *_args, **kwargs):
            normalized = " ".join(str(query).split()).lower()
            if normalized == "select 1":
                return [[1]]
            if "sum(data_free)" in normalized:
                return [[0]]
            if normalized == "select @@performance_schema":
                return [[1]]
            if "performance_schema.setup_instruments" in normalized:
                return [["YES"]]
            if normalized == "show global status like 'performance_schema_metadata_lock_lost'":
                return [["Performance_schema_metadata_lock_lost", "0"]]
            if "innodb_trx" in normalized or "metadata_locks" in normalized:
                return [[0]]
            if "tab aos transactional outbox" in normalized or "tabaos transactional outbox" in normalized:
                return []
            return []

        backup_report = {
            "ready": backup_ready,
            "summary": {"unhealthy": 0 if backup_ready else 1},
        }
        with (
            patch.dict("os.environ", {"AOS_ENVIRONMENT": environment}, clear=False),
            patch.object(db, "sql", side_effect=sql),
            patch.object(db, "table_exists", return_value=True),
            patch.object(db, "get_table_columns", return_value=list(migration_preflight._REQUIRED_OUTBOX_COLUMNS)),
            patch("frappe.modules.patch_handler.get_all_patches", return_value=[]),
            patch("aos.utils.migration_preflight.frappe.get_all", return_value=[]),
            patch("aos.utils.migration_preflight.frappe.get_installed_apps", return_value=["frappe", "aos"]),
            patch("aos.utils.migration_preflight.shutil.disk_usage") as disk_usage,
            patch("aos.utils.backup_readiness.validate_backup_readiness", return_value=backup_report),
            patch("frappe.utils.background_jobs.get_workers", return_value=[object()]),
            patch("aos.utils.migration_preflight._previous_migration_failure_check", return_value=(True, "clear", {})),
        ):
            disk_usage.return_value.free = 20 * 1024**3
            return migration_preflight.validate_migration_preflight()

    def test_production_backup_readiness_failure_is_a_blocker(self):
        report = self._run(environment="production", backup_ready=False)
        self.assertFalse(report["ready"])
        self.assertIn("backup_readiness", report["blockers"])

    def test_staging_backup_readiness_failure_is_an_explicit_warning(self):
        report = self._run(environment="staging", backup_ready=False)
        self.assertTrue(report["ready"], report)
        self.assertIn("backup_readiness", report["warnings"])
        backup = next(item for item in report["checks"] if item["name"] == "backup_readiness")
        self.assertEqual(backup["details"]["unhealthy_check_count"], 1)


    def test_metadata_lock_instrumentation_is_required(self):
        scenarios = {
            "schema_off": [[[0]], [["YES"]], [["Performance_schema_metadata_lock_lost", "0"]]],
            "instrument_off": [[[1]], [["NO"]], [["Performance_schema_metadata_lock_lost", "0"]]],
            "instrument_missing": [[[1]], [], [["Performance_schema_metadata_lock_lost", "0"]]],
            "counter_missing": [[[1]], [["YES"]], []],
            "counter_lost": [[[1]], [["YES"]], [["Performance_schema_metadata_lock_lost", "2"]]],
            "counter_invalid": [[[1]], [["YES"]], [["Performance_schema_metadata_lock_lost", "not-number"]]],
            "healthy": [[[1]], [["YES"]], [["Performance_schema_metadata_lock_lost", "0"]]],
        }
        for scenario, results in scenarios.items():
            with self.subTest(scenario=scenario):
                with patch.object(frappe.db, "sql", side_effect=results):
                    ready, _message, _details = migration_preflight._metadata_lock_instrumentation_check()
                self.assertEqual(ready, scenario == "healthy")
        with patch.object(frappe.db, "sql", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                migration_preflight._metadata_lock_instrumentation_check()

    def test_metadata_lock_instrumentation_failure_blocks_preflight(self):
        report = self._run(environment="staging", backup_ready=True)
        self.assertTrue(report["ready"], report)
        check = next(item for item in report["checks"] if item["name"] == "metadata_lock_instrumentation")
        self.assertTrue(check["ready"])

    def test_patch_inventory_uses_pinned_frappe_api_and_excludes_applied_patches(self):
        with (
            patch("frappe.modules.patch_handler.get_all_patches", return_value=["a.first", "a.second"]),
            patch("aos.utils.migration_preflight.frappe.get_all", return_value=["a.first"]) as patch_log,
        ):
            ready, _message, details = migration_preflight._pending_patch_check()
        self.assertTrue(ready)
        self.assertEqual(details["pending_patch_count"], 1)
        self.assertTrue(details["manual_patch_review_required"])
        patch_log.assert_called_once_with(
            "Patch Log", filters={"skipped": 0}, fields="patch", pluck="patch"
        )

    def test_patch_inventory_fails_closed_if_frappe_cannot_read_patch_log(self):
        with (
            patch("frappe.modules.patch_handler.get_all_patches", return_value=["a.first"]),
            patch("aos.utils.migration_preflight.frappe.get_all", side_effect=RuntimeError("denied")),
        ):
            with self.assertRaises(RuntimeError):
                migration_preflight._pending_patch_check()

    def test_previous_migration_failure_marker_fails_closed(self):
        with self.subTest("marker"):
            with patch.dict(
                "os.environ",
                {"AOS_MIGRATION_FAILURE_MARKER": "/tmp/aos-test-migration-failure"},
                clear=False,
            ):
                with patch.object(Path, "is_file", return_value=True):
                    ready, message, details = migration_preflight._previous_migration_failure_check()
        self.assertFalse(ready)
        self.assertTrue(details["failure_marker_present"])
        self.assertNotIn("/tmp", message)

    def test_outbox_dead_letters_and_excess_backlog_are_blockers(self):
        rows = [
            frappe._dict(status="Queued", total=5001),
            frappe._dict(status="Dead Letter", total=1),
            frappe._dict(status="Manual Review", total=1),
        ]
        with (
            patch.dict("os.environ", {"AOS_MIGRATION_MAX_OUTBOX_BACKLOG": "5000"}, clear=False),
            patch.object(frappe.db, "sql", return_value=rows),
        ):
            ready, _message, details = migration_preflight._outbox_check()
        self.assertFalse(ready)
        self.assertEqual(details["dead_letter_count"], 1)
        self.assertEqual(details["manual_review_count"], 1)
        self.assertEqual(details["queued_retryable_count"], 5001)

    def test_preflight_discloses_manual_review_limitation(self):
        report = self._run(environment="production", backup_ready=True)
        self.assertTrue(report["manual_review_required"])
        manual = next(item for item in report["checks"] if item["name"] == "manual_migration_review")
        self.assertIn("cannot prove", manual["message"].lower())
