from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.utils.backup_readiness import validate_backup_readiness


class TestBackupReadiness(FrappeTestCase):
	"""Focused tests for backup/restore readiness diagnostics."""

	def setUp(self):
		frappe.local.response = {}
		self.now = datetime(2026, 7, 8, 10, 0, 0, tzinfo=UTC)

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

		for path in [
			repo,
			bench / "sites" / site / "public" / "files",
			bench / "sites" / site / "private" / "files",
		]:
			path.mkdir(parents=True, exist_ok=True)
		(bench / "sites" / site / "site_config.json").write_text('{"db_name":"redacted"}', encoding="utf-8")

		for rel in [
			"infra/backup/backup.sh",
			"infra/backup/offsite-copy.sh",
			"infra/backup/restore.sh",
			"infra/backup/verify-backup.sh",
			"infra/backup/restore-rehearsal-checklist.sh",
			"infra/backup/backup_artifacts.py",
			"infra/backup/backup_crypto.py",
			"infra/backup/rehearsal_policy.py",
		]:
			script = repo / rel
			script.parent.mkdir(parents=True, exist_ok=True)
			script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
			os.chmod(script, 0o644 if rel.endswith("rehearsal_policy.py") else 0o755)

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
		(backup_dir / "config" / "aos.env").write_text(
			"MINIO_ROOT_PASSWORD=super-secret-value\n", encoding="utf-8"
		)
		(backup_dir / "config" / "site_config.json").write_text(
			'{"db_password":"super-secret-value"}', encoding="utf-8"
		)

		marker_time = self.now - timedelta(days=restore_marker_age_days)
		marker = backup_root / "restore-rehearsal-passed.env"
		restore_completed = marker_time - timedelta(minutes=5)
		marker.write_text(
			"\n".join(
				[
					"RESTORE_REHEARSAL_VERSION=3",
					"RESTORE_REHEARSAL_ENVIRONMENT=rehearsal",
					"RESTORE_REHEARSAL_MODE=full",
					f"RESTORE_REHEARSAL_PASSED_AT_UTC={marker_time.strftime('%Y-%m-%dT%H:%M:%SZ')}",
					"BACKUP_ID=20260708T090000Z",
					"BACKUP_CREATED_AT_UTC=20260708T090000Z",
					f"RESTORE_COMPLETED_AT_UTC={restore_completed.strftime('%Y-%m-%dT%H:%M:%SZ')}",
					"DATABASE_RESTORE_RESULT=restored",
					"PUBLIC_ARCHIVE_PRESENT=true",
					"PUBLIC_FILES_RESTORE_RESULT=checksum_matched",
					"PUBLIC_REPRESENTATIVE_FILE_VERIFY_RESULT=passed",
					"PRIVATE_ARCHIVE_PRESENT=true",
					"PRIVATE_FILES_RESTORE_RESULT=checksum_matched",
					"PRIVATE_REPRESENTATIVE_FILE_VERIFY_RESULT=passed",
					"CHECKSUM_VERIFICATION_RESULT=passed",
					"MIGRATION_RESULT=passed",
					"PRODUCTION_CONFIG_RESULT=passed",
					"HEALTH_CHECK_RESULT=passed",
					"JOB_MONITORING_RESULT=passed",
				]
			)
			+ "\n",
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
			"RESTORE_REHEARSAL_REQUIRE_FILES": "true",
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
		offsite = next(
			check for check in report.get("checks", []) if check.get("name") == "offsite_backup_scope"
		)
		self.assertEqual(offsite.get("status"), "healthy", report)
		self.assertTrue((offsite.get("details") or {}).get("marker_backup_matches_latest"), report)
		serialized = str(report)
		self.assertNotIn("super-secret-value", serialized)
		self.assertNotIn("db_password", serialized)
		self.assertNotIn("MINIO_ROOT_PASSWORD", serialized)

	def test_python_rehearsal_helper_must_be_readable_not_executable(self):
		temp, env, _backup_dir = self._make_layout()
		self.addCleanup(temp.cleanup)
		helper = Path(env["AOS_REPO_ROOT"]) / "infra/backup/rehearsal_policy.py"
		self.assertFalse(os.access(helper, os.X_OK))
		report = validate_backup_readiness(backup_env=env, now=self.now)
		tooling = next(
			check for check in report["checks"] if check["name"] == "backup_restore_tooling"
		)
		self.assertEqual(tooling["status"], "healthy", tooling)

	def test_directly_invoked_backup_helper_still_requires_execute_permission(self):
		temp, env, _backup_dir = self._make_layout()
		self.addCleanup(temp.cleanup)
		entrypoint = Path(env["AOS_REPO_ROOT"]) / "infra/backup/backup_crypto.py"
		entrypoint.chmod(0o644)
		report = validate_backup_readiness(backup_env=env, now=self.now)
		tooling = next(
			check for check in report["checks"] if check["name"] == "backup_restore_tooling"
		)
		self.assertEqual(tooling["status"], "degraded", tooling)
		self.assertIn("infra/backup/backup_crypto.py", tooling["details"]["not_executable_scripts"])

	def test_frappe_native_tgz_file_archives_are_detected(self):
		temp, env, _backup_dir = self._make_layout()
		self.addCleanup(temp.cleanup)

		report = validate_backup_readiness(backup_env=env, now=self.now)

		latest = next(
			check for check in report.get("checks", []) if check.get("name") == "latest_backup_artifact"
		)
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
		latest = [
			check for check in report.get("checks", []) if check.get("name") == "latest_backup_artifact"
		]
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
		offsite = next(
			check for check in report.get("checks", []) if check.get("name") == "offsite_backup_scope"
		)
		self.assertEqual(offsite.get("status"), "unhealthy", report)

	def test_offsite_marker_must_match_latest_backup_when_marker_has_backup_id(self):
		temp, env, _backup_dir = self._make_layout(offsite_backup_id="20260707T090000Z")
		self.addCleanup(temp.cleanup)

		report = validate_backup_readiness(backup_env=env, now=self.now)

		self.assertFalse(report.get("ready"), report)
		offsite = next(
			check for check in report.get("checks", []) if check.get("name") == "offsite_backup_scope"
		)
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
		offsite = next(
			check for check in report.get("checks", []) if check.get("name") == "offsite_backup_scope"
		)
		self.assertEqual(offsite.get("status"), "healthy", report)
		self.assertEqual((offsite.get("details") or {}).get("offsite_backup_mode"), "s3")
		self.assertNotIn("s3.example.test", str(report))

	def test_missing_rsync_target_makes_configured_offsite_unready(self):
		temp, env, _backup_dir = self._make_layout()
		self.addCleanup(temp.cleanup)
		env.pop("OFFSITE_RSYNC_TARGET", None)

		report = validate_backup_readiness(backup_env=env, now=self.now)

		self.assertFalse(report.get("ready"), report)
		offsite = next(
			check for check in report.get("checks", []) if check.get("name") == "offsite_backup_scope"
		)
		self.assertEqual(offsite.get("status"), "unhealthy", report)
		self.assertIn("OFFSITE_RSYNC_TARGET", str(offsite))

	def _enable_production_encryption(self, env, backup_dir):
		import hashlib

		encrypted_root = Path(env["BACKUP_ROOT"]) / "encrypted"
		encrypted_root.mkdir(parents=True, exist_ok=True)
		artifact = encrypted_root / f"{backup_dir.name}.tar.gz.age"
		artifact.write_bytes(b"encrypted-backup")
		digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
		Path(str(artifact) + ".sha256").write_text(f"{digest}  {artifact.name}\n", encoding="utf-8")
		Path(str(artifact) + ".metadata.env").write_text(
			"\n".join(
				[
					f"BACKUP_ID={backup_dir.name}",
					"CREATED_AT_UTC=20260708T090000Z",
					"DATABASE_BACKUP_PRESENT=true",
					"PUBLIC_FILES_BACKUP_PRESENT=true",
					"PRIVATE_FILES_BACKUP_PRESENT=true",
					"MINIO_ARCHIVE_PRESENT=true",
					"CONFIGURATION_SNAPSHOT_PRESENT=true",
					"PLAINTEXT_LOCAL_RETAINED=false",
					"LOCAL_RETENTION_MODE=encrypted-artifact",
					"ENCRYPTED_ARTIFACT_VERIFIED=true",
				]
			)
			+ "\n",
			encoding="utf-8",
		)
		env.update(
			{
				"AOS_ENVIRONMENT": "production",
				"BACKUP_ENCRYPTION_REQUIRED": "true",
				"BACKUP_ENCRYPTION_METHOD": "age",
				"BACKUP_AGE_RECIPIENT": "age1" + "a" * 50,
				"AGE_BINARY": "/usr/bin/age",
				"ENCRYPTED_BACKUP_ROOT": str(encrypted_root),
				"BACKUP_LOCAL_RETENTION_MODE": "encrypted-artifact",
				"PLAINTEXT_WORK_ROOT": str(Path(env["BACKUP_ROOT"]) / ".plaintext-work"),
				"RESTORE_REHEARSAL_REQUIRE_FILES": "true",
			}
		)
		# Production retention removes the verified plaintext set after encryption.
		import shutil
		shutil.rmtree(backup_dir)
		marker = Path(env["OFFSITE_SYNC_MARKER"])
		marker.write_text(
			marker.read_text(encoding="utf-8")
			+ "OFFSITE_ENCRYPTION_METHOD=age\n"
			+ "OFFSITE_ARTIFACT_KIND=encrypted-age\n"
			+ f"OFFSITE_ARTIFACT_NAME={artifact.name}\n"
			+ f"OFFSITE_ENCRYPTED_SHA256={digest}\n",
			encoding="utf-8",
		)
		return artifact

	def test_production_encryption_missing_fails_closed(self):
		temp, env, _backup_dir = self._make_layout()
		self.addCleanup(temp.cleanup)
		env["AOS_ENVIRONMENT"] = "production"
		env["BACKUP_ENCRYPTION_REQUIRED"] = "true"
		report = validate_backup_readiness(backup_env=env, now=self.now)
		encryption = next(check for check in report["checks"] if check["name"] == "backup_encryption")
		self.assertFalse(report["ready"], report)
		self.assertEqual(encryption["status"], "unhealthy")
		self.assertIn("production_encryption_disabled", encryption["details"]["errors"])

	def test_placeholder_encryption_recipient_fails_without_leaking_value(self):
		temp, env, backup_dir = self._make_layout()
		self.addCleanup(temp.cleanup)
		self._enable_production_encryption(env, backup_dir)
		env["BACKUP_AGE_RECIPIENT"] = "age1-placeholder-change-me"
		with patch("aos.utils.backup_readiness.shutil.which", return_value="/usr/bin/age"):
			report = validate_backup_readiness(backup_env=env, now=self.now)
		encryption = next(check for check in report["checks"] if check["name"] == "backup_encryption")
		self.assertEqual(encryption["status"], "unhealthy")
		self.assertIn("invalid_age_recipient", encryption["details"]["errors"])
		self.assertNotIn("age1-placeholder-change-me", str(report))

	def test_production_encryption_and_integrity_are_reported_ready(self):
		temp, env, backup_dir = self._make_layout()
		self.addCleanup(temp.cleanup)
		self._enable_production_encryption(env, backup_dir)
		with patch("aos.utils.backup_readiness.shutil.which", return_value="/usr/bin/age"):
			report = validate_backup_readiness(backup_env=env, now=self.now)
		self.assertTrue(report["ready"], report)
		encryption = next(check for check in report["checks"] if check["name"] == "backup_encryption")
		self.assertTrue(encryption["details"]["encrypted_artifact_integrity_verified"])

	def test_production_offsite_marker_checksum_must_match_latest_artifact(self):
		temp, env, backup_dir = self._make_layout()
		self.addCleanup(temp.cleanup)
		self._enable_production_encryption(env, backup_dir)
		marker = Path(env["OFFSITE_SYNC_MARKER"])
		marker.write_text(
			marker.read_text(encoding="utf-8").replace(
				"OFFSITE_ENCRYPTED_SHA256=", "OFFSITE_ENCRYPTED_SHA256=" + "0" * 64 + "#"
			),
			encoding="utf-8",
		)
		with patch("aos.utils.backup_readiness.shutil.which", return_value="/usr/bin/age"):
			report = validate_backup_readiness(backup_env=env, now=self.now)
		offsite = next(check for check in report["checks"] if check["name"] == "offsite_backup_scope")
		self.assertEqual(offsite["status"], "unhealthy", report)
		self.assertFalse(offsite["details"]["marker_checksum_matches_latest"])

	def test_rehearsal_marker_must_identify_a_non_production_environment(self):
		temp, env, _backup_dir = self._make_layout()
		self.addCleanup(temp.cleanup)
		marker = Path(env["RESTORE_REHEARSAL_MARKER"])
		marker.write_text(
			marker.read_text().replace(
				"RESTORE_REHEARSAL_ENVIRONMENT=rehearsal",
				"RESTORE_REHEARSAL_ENVIRONMENT=production",
			)
		)
		report = validate_backup_readiness(backup_env=env, now=self.now)
		rehearsal = next(check for check in report["checks"] if check["name"] == "restore_rehearsal")
		self.assertEqual(rehearsal["status"], "unhealthy")
		self.assertIn(
			"invalid_or_production_rehearsal_environment",
			rehearsal["details"]["errors"],
		)

	def test_rehearsal_marker_for_another_backup_is_rejected(self):
		temp, env, _backup_dir = self._make_layout()
		self.addCleanup(temp.cleanup)
		marker = Path(env["RESTORE_REHEARSAL_MARKER"])
		marker.write_text(
			marker.read_text().replace("BACKUP_ID=20260708T090000Z", "BACKUP_ID=20260707T090000Z")
		)
		report = validate_backup_readiness(backup_env=env, now=self.now)
		rehearsal = next(check for check in report["checks"] if check["name"] == "restore_rehearsal")
		self.assertEqual(rehearsal["status"], "unhealthy")
		self.assertIn("marker_backup_mismatch", rehearsal["details"]["errors"])

	def test_rehearsal_marker_created_before_restore_completion_is_rejected(self):
		temp, env, _backup_dir = self._make_layout()
		self.addCleanup(temp.cleanup)
		marker = Path(env["RESTORE_REHEARSAL_MARKER"])
		marker.write_text(
			marker.read_text().replace(
				"RESTORE_COMPLETED_AT_UTC=2026-07-08T09:55:00Z",
				"RESTORE_COMPLETED_AT_UTC=2026-07-08T10:05:00Z",
			)
		)
		report = validate_backup_readiness(backup_env=env, now=self.now)
		rehearsal = next(check for check in report["checks"] if check["name"] == "restore_rehearsal")
		self.assertEqual(rehearsal["status"], "unhealthy")
		self.assertIn("marker_predates_restore_completion", rehearsal["details"]["errors"])

	def test_rehearsal_missing_private_file_evidence_is_rejected(self):
		temp, env, _backup_dir = self._make_layout()
		self.addCleanup(temp.cleanup)
		marker = Path(env["RESTORE_REHEARSAL_MARKER"])
		marker.write_text(
			marker.read_text().replace(
				"PRIVATE_FILES_RESTORE_RESULT=checksum_matched", "PRIVATE_FILES_RESTORE_RESULT=missing"
			)
		)
		report = validate_backup_readiness(backup_env=env, now=self.now)
		rehearsal = next(check for check in report["checks"] if check["name"] == "restore_rehearsal")
		self.assertEqual(rehearsal["status"], "unhealthy")
		self.assertIn("private_file_restore_evidence", rehearsal["details"]["errors"])
