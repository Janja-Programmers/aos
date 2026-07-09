from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.diagnostics import get_backup_readiness_status
from aos.utils.backup_readiness import validate_backup_readiness


class TestBackupReadiness(FrappeTestCase):
    """Focused tests for backup/restore readiness diagnostics."""

    def setUp(self):
        frappe.local.response = {}
        self.now = datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc)

    def _make_layout(
        self,
        *,
        include_minio: bool = True,
        restore_marker_age_days: int = 0,
        include_offsite_marker: bool = True,
        offsite_marker_age_hours: int = 1,
        offsite_backup_id: str = "20260708T090000Z",
        offsite_mode: str = "rsync",
    ):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        repo = root / "repo"
        bench = root / "frappe-bench"
        site = "aos-staging.test"
        backup_root = root / "backups"
        backup_dir = backup_root / "20260708T090000Z"

        for path in [repo, bench / "sites" / site / "public" / "files", bench / "sites" / site / "private" / "files"]:
            path.mkdir(parents=True, exist_ok=True)
        (bench / "sites" / site / "site_config.json").write_text('{"db_name":"redacted"}', encoding="utf-8")

        for rel in [
            "infra/backup/backup.sh",
            "infra/backup/offsite-copy.sh",
            "infra/backup/restore.sh",
            "infra/backup/verify-backup.sh",
            "infra/backup/restore-rehearsal-checklist.sh",
        ]:
            script = repo / rel
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            os.chmod(script, 0o755)

        (backup_dir / "frappe").mkdir(parents=True, exist_ok=True)
        (backup_dir / "docker").mkdir(parents=True, exist_ok=True)
        (backup_dir / "config").mkdir(parents=True, exist_ok=True)
        (backup_dir / "metadata.env").write_text(
            "BACKUP_VERSION=1\nCREATED_AT_UTC=20260708T090000Z\nFRAPPE_SITE=aos-staging.test\n",
            encoding="utf-8",
        )
        (backup_dir / "SHA256SUMS").write_text("placeholder  metadata.env\n", encoding="utf-8")
        (backup_dir / "VERIFIED_AT_UTC").write_text("2026-07-08T09:01:00Z\n", encoding="utf-8")
        (backup_dir / "frappe" / "20260708_090000-database.sql.gz").write_text("db", encoding="utf-8")
        # Match Frappe native backup naming used by backup.sh.
        (backup_dir / "frappe" / "20260708_090000-files.tgz").write_text("public", encoding="utf-8")
        (backup_dir / "frappe" / "20260708_090000-private-files.tgz").write_text("private", encoding="utf-8")
        if include_minio:
            (backup_dir / "docker" / "minio_data.tar.gz").write_text("minio", encoding="utf-8")
        (backup_dir / "config" / "aos.env").write_text("MINIO_ROOT_PASSWORD=super-secret-value\n", encoding="utf-8")
        (backup_dir / "config" / "site_config.json").write_text('{"db_password":"super-secret-value"}', encoding="utf-8")

        marker_time = self.now - timedelta(days=restore_marker_age_days)
        marker = backup_root / "restore-rehearsal-passed.env"
        marker.write_text(
            f"RESTORE_REHEARSAL_PASSED_AT_UTC={marker_time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n",
            encoding="utf-8",
        )

        offsite_marker = backup_root / "offsite-sync-passed.env"
        if include_offsite_marker:
            offsite_time = self.now - timedelta(hours=offsite_marker_age_hours)
            offsite_marker.write_text(
                "\n".join(
                    [
                        f"OFFSITE_SYNC_PASSED_AT_UTC={offsite_time.strftime('%Y-%m-%dT%H:%M:%SZ')}",
                        f"OFFSITE_BACKUP_ID={offsite_backup_id}",
                        f"OFFSITE_BACKUP_MODE={offsite_mode}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

        env = {
            "AOS_REPO_ROOT": str(repo),
            "FRAPPE_BENCH_ROOT": str(bench),
            "FRAPPE_SITE": site,
            "BACKUP_ROOT": str(backup_root),
            "INCLUDE_MINIO_DATA": "true",
            "INCLUDE_CONFIGURATION": "true",
            "OFFSITE_BACKUP_MODE": offsite_mode,
            "OFFSITE_RSYNC_TARGET": "backup@example.com:/srv/aos-backups",
            "OFFSITE_SYNC_MARKER": str(offsite_marker),
            "RESTORE_REHEARSAL_MARKER": str(marker),
            "MINIO_ROOT_PASSWORD": "super-secret-value",
        }
        return temp, env, backup_dir

    def test_backup_readiness_all_clear_and_redacted(self):
        temp, env, _backup_dir = self._make_layout()
        self.addCleanup(temp.cleanup)

        report = validate_backup_readiness(backup_env=env, now=self.now)

        self.assertTrue(report.get("ready"), report)
        names = {check.get("name") for check in report.get("checks", [])}
        self.assertIn("backup_environment", names)
        self.assertIn("backup_restore_tooling", names)
        self.assertIn("frappe_backup_scope", names)
        self.assertIn("minio_backup_scope", names)
        self.assertIn("configuration_backup_scope", names)
        self.assertIn("offsite_backup_scope", names)
        self.assertIn("latest_backup_artifact", names)
        self.assertIn("restore_rehearsal", names)
        offsite = [check for check in report.get("checks", []) if check.get("name") == "offsite_backup_scope"][0]
        self.assertEqual(offsite.get("status"), "healthy", report)
        self.assertTrue((offsite.get("details") or {}).get("marker_backup_matches_latest"), report)
        serialized = str(report)
        self.assertNotIn("super-secret-value", serialized)
        self.assertNotIn("db_password", serialized)
        self.assertNotIn("MINIO_ROOT_PASSWORD", serialized)


    def test_frappe_native_tgz_file_archives_are_detected(self):
        temp, env, _backup_dir = self._make_layout()
        self.addCleanup(temp.cleanup)

        report = validate_backup_readiness(backup_env=env, now=self.now)

        latest = [check for check in report.get("checks", []) if check.get("name") == "latest_backup_artifact"][0]
        details = latest.get("details") or {}
        self.assertEqual(latest.get("status"), "healthy", report)
        self.assertTrue(details.get("has_public_files_backup"), report)
        self.assertTrue(details.get("has_private_files_backup"), report)

    def test_missing_minio_archive_makes_report_unready(self):
        temp, env, _backup_dir = self._make_layout(include_minio=False)
        self.addCleanup(temp.cleanup)

        report = validate_backup_readiness(backup_env=env, now=self.now)

        self.assertFalse(report.get("ready"), report)
        statuses = {check.get("name"): check.get("status") for check in report.get("checks", [])}
        self.assertEqual(statuses.get("latest_backup_artifact"), "unhealthy")
        serialized = str(report)
        self.assertIn("minio_data_archive", serialized)
        self.assertNotIn("super-secret-value", serialized)

    def test_missing_restore_rehearsal_marker_makes_report_unready(self):
        temp, env, _backup_dir = self._make_layout()
        self.addCleanup(temp.cleanup)
        Path(env["RESTORE_REHEARSAL_MARKER"]).unlink()

        report = validate_backup_readiness(backup_env=env, now=self.now)

        self.assertFalse(report.get("ready"), report)
        statuses = {check.get("name"): check.get("status") for check in report.get("checks", [])}
        self.assertEqual(statuses.get("restore_rehearsal"), "unhealthy")

    def test_stale_backup_artifact_makes_report_unready(self):
        temp, env, _backup_dir = self._make_layout()
        self.addCleanup(temp.cleanup)

        report = validate_backup_readiness(
            backup_env=env,
            now=self.now + timedelta(days=3),
            max_backup_age_hours=26,
        )

        self.assertFalse(report.get("ready"), report)
        latest = [check for check in report.get("checks", []) if check.get("name") == "latest_backup_artifact"]
        self.assertEqual(latest[0].get("status"), "unhealthy")


    def test_missing_offsite_marker_makes_report_unready_when_configured(self):
        temp, env, _backup_dir = self._make_layout(include_offsite_marker=False)
        self.addCleanup(temp.cleanup)

        report = validate_backup_readiness(backup_env=env, now=self.now)

        self.assertFalse(report.get("ready"), report)
        statuses = {check.get("name"): check.get("status") for check in report.get("checks", [])}
        self.assertEqual(statuses.get("offsite_backup_scope"), "unhealthy")
        serialized = str(report)
        self.assertNotIn("backup@example.com", serialized)

    def test_stale_offsite_marker_makes_report_unready(self):
        temp, env, _backup_dir = self._make_layout(offsite_marker_age_hours=72)
        self.addCleanup(temp.cleanup)

        report = validate_backup_readiness(backup_env=env, now=self.now, offsite_max_sync_age_hours=26)

        self.assertFalse(report.get("ready"), report)
        offsite = [check for check in report.get("checks", []) if check.get("name") == "offsite_backup_scope"][0]
        self.assertEqual(offsite.get("status"), "unhealthy", report)

    def test_offsite_marker_must_match_latest_backup_when_marker_has_backup_id(self):
        temp, env, _backup_dir = self._make_layout(offsite_backup_id="20260707T090000Z")
        self.addCleanup(temp.cleanup)

        report = validate_backup_readiness(backup_env=env, now=self.now)

        self.assertFalse(report.get("ready"), report)
        offsite = [check for check in report.get("checks", []) if check.get("name") == "offsite_backup_scope"][0]
        self.assertEqual(offsite.get("status"), "unhealthy", report)
        self.assertFalse((offsite.get("details") or {}).get("marker_backup_matches_latest"), report)

    def test_s3_offsite_configuration_is_accepted_with_fresh_marker(self):
        temp, env, _backup_dir = self._make_layout(offsite_mode="s3")
        self.addCleanup(temp.cleanup)
        env.pop("OFFSITE_RSYNC_TARGET", None)
        env["OFFSITE_S3_BUCKET"] = "aos-production-backups"
        env["OFFSITE_S3_ENDPOINT_URL"] = "https://s3.example.test"

        report = validate_backup_readiness(backup_env=env, now=self.now)

        self.assertTrue(report.get("ready"), report)
        offsite = [check for check in report.get("checks", []) if check.get("name") == "offsite_backup_scope"][0]
        self.assertEqual(offsite.get("status"), "healthy", report)
        self.assertEqual((offsite.get("details") or {}).get("offsite_backup_mode"), "s3")
        self.assertNotIn("s3.example.test", str(report))

    def test_missing_rsync_target_makes_configured_offsite_unready(self):
        temp, env, _backup_dir = self._make_layout()
        self.addCleanup(temp.cleanup)
        env.pop("OFFSITE_RSYNC_TARGET", None)

        report = validate_backup_readiness(backup_env=env, now=self.now)

        self.assertFalse(report.get("ready"), report)
        offsite = [check for check in report.get("checks", []) if check.get("name") == "offsite_backup_scope"][0]
        self.assertEqual(offsite.get("status"), "unhealthy", report)
        self.assertIn("OFFSITE_RSYNC_TARGET", str(offsite))

    def test_admin_diagnostic_requires_system_manager(self):
        frappe.set_user("Guest")
        response = get_backup_readiness_status()
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "PERMISSION_DENIED")

    def test_admin_diagnostic_returns_redacted_report_for_system_manager(self):
        frappe.set_user("Administrator")
        expected = {
            "ready": True,
            "summary": {"checks": 1, "healthy": 1, "degraded": 0, "unhealthy": 0, "skipped": 0},
            "checks": [],
        }
        with patch("aos.api.diagnostics.status.validate_backup_readiness", return_value=expected):
            response = get_backup_readiness_status()
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response.get("data"), expected)
