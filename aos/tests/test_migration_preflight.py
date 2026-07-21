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
            if "innodb_trx" in normalized or "metadata_locks" in normalized:
                return [[0]]
            if "tab aos transactional outbox" in normalized or "tabaos transactional outbox" in normalized:
                return []
            return []

        backup_report = {
            "ready": backup_ready,
            "counts": {"unhealthy": 0 if backup_ready else 1},
        }
        with (
            patch.dict("os.environ", {"AOS_ENVIRONMENT": environment}, clear=False),
            patch.object(db, "sql", side_effect=sql),
            patch.object(db, "table_exists", return_value=True),
            patch.object(db, "get_table_columns", return_value=list(migration_preflight._REQUIRED_OUTBOX_COLUMNS)),
            patch("aos.utils.migration_preflight.frappe.get_attr", return_value=lambda: []),
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
        ]
        with (
            patch.dict("os.environ", {"AOS_MIGRATION_MAX_OUTBOX_BACKLOG": "5000"}, clear=False),
            patch.object(frappe.db, "sql", return_value=rows),
        ):
            ready, _message, details = migration_preflight._outbox_check()
        self.assertFalse(ready)
        self.assertEqual(details["dead_letter_count"], 1)
        self.assertEqual(details["queued_retryable_count"], 5001)

    def test_preflight_discloses_manual_review_limitation(self):
        report = self._run(environment="production", backup_ready=True)
        self.assertTrue(report["manual_review_required"])
        manual = next(item for item in report["checks"] if item["name"] == "manual_migration_review")
        self.assertIn("cannot prove", manual["message"].lower())
