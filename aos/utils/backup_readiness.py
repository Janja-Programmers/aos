"""Backup and restore-readiness validation for AOS production operations.

This module is intentionally read-only. It checks whether the deployment has
usable backup tooling, backup configuration, a recent verified backup artifact,
and evidence of a recent restore rehearsal without exposing environment values,
secrets, site-config contents, private file paths, database credentials, or raw
backup file contents.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import frappe
from frappe.utils import cint, now_datetime

BackupStatus = str

DEFAULT_BACKUP_ENV_FILE = "/etc/aos/backup.env"
DEFAULT_MAX_BACKUP_AGE_HOURS = 26
DEFAULT_RESTORE_REHEARSAL_MAX_AGE_DAYS = 30
DEFAULT_OFFSITE_MAX_SYNC_AGE_HOURS = 26

REQUIRED_BACKUP_ENV_KEYS: tuple[str, ...] = (
	"AOS_REPO_ROOT",
	"FRAPPE_BENCH_ROOT",
	"FRAPPE_SITE",
	"BACKUP_ROOT",
)

BACKUP_SCRIPT_PATHS: tuple[str, ...] = (
	"infra/backup/backup.sh",
	"infra/backup/offsite-copy.sh",
	"infra/backup/restore.sh",
	"infra/backup/verify-backup.sh",
	"infra/backup/restore-rehearsal-checklist.sh",
	"infra/backup/backup_artifacts.py",
	"infra/backup/backup_crypto.py",
	"infra/backup/rehearsal_policy.py",
)

# This policy helper is invoked by python3, not as an executable entrypoint.
BACKUP_READABLE_HELPERS = frozenset({"infra/backup/rehearsal_policy.py"})


SENSITIVE_KEY_RE = re.compile(
	r"(secret|token|password|passwd|pwd|api[_-]?key|private[_-]?key|credential|signature)",
	re.IGNORECASE,
)


def _clean(value: Any) -> str:
	return str(value or "").strip()


def _bool_value(value: Any, default: bool = False) -> bool:
	text = _clean(value)
	if not text:
		return bool(default)
	return text.lower() in {"1", "true", "yes", "y", "on"}


def _safe_int(value: Any, default: int = 0) -> int:
	try:
		return int(value)
	except Exception:
		return int(default)


def _public_key(key: str) -> str:
	text = _clean(key)
	return "[redacted]" if SENSITIVE_KEY_RE.search(text) else text


def _safe_keys(keys: list[str] | tuple[str, ...]) -> list[str]:
	return [_public_key(key) for key in keys if _clean(key)]


def _strip_quotes(value: str) -> str:
	text = value.strip()
	if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
		return text[1:-1]
	return text


def _parse_env_file(path: str) -> dict[str, str]:
	values: dict[str, str] = {}
	with open(path, encoding="utf-8") as handle:
		for raw_line in handle.read().splitlines():
			line = raw_line.strip()
			if not line or line.startswith("#") or "=" not in line:
				continue
			key, value = line.split("=", 1)
			key = key.strip()
			if not key or key.startswith("#"):
				continue
			values[key] = _strip_quotes(value)
	return values


def _resolve_backup_env(
	*,
	backup_env: Mapping[str, Any] | None,
	backup_env_path: str | None,
) -> tuple[dict[str, str], str | None, str | None]:
	if backup_env is not None:
		return {str(key): _clean(value) for key, value in backup_env.items()}, backup_env_path, None

	path = _clean(backup_env_path) or _clean(os.environ.get("AOS_BACKUP_ENV_FILE")) or DEFAULT_BACKUP_ENV_FILE
	try:
		return _parse_env_file(path), path, None
	except FileNotFoundError:
		return {}, path, "missing"
	except PermissionError:
		return {}, path, "permission_denied"
	except Exception:
		return {}, path, "unreadable"


def _check(
	checks: list[dict[str, Any]],
	*,
	name: str,
	category: str,
	status: BackupStatus,
	message: str,
	details: Mapping[str, Any] | None = None,
) -> None:
	checks.append(
		{
			"name": name,
			"category": category,
			"status": status,
			"message": message,
			"details": dict(details or {}),
		}
	)


def _file_exists(path: str | Path) -> bool:
	try:
		return Path(path).exists()
	except Exception:
		return False


def _is_executable_file(path: str | Path) -> bool:
	try:
		candidate = Path(path)
		return candidate.is_file() and os.access(candidate, os.X_OK)
	except Exception:
		return False


def _dir_exists(path: str | Path) -> bool:
	try:
		return Path(path).is_dir()
	except Exception:
		return False


def _parse_backup_created_at(metadata: Mapping[str, str], backup_dir: Path) -> datetime | None:
	raw = _clean(metadata.get("CREATED_AT_UTC"))
	if raw:
		for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ"):
			try:
				return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
			except Exception:
				pass
	try:
		return datetime.fromtimestamp(backup_dir.stat().st_mtime, tz=UTC)
	except Exception:
		return None


def _parse_marker_time(path: Path) -> datetime | None:
	try:
		text = path.read_text(encoding="utf-8", errors="ignore")[:1000]
	except Exception:
		return None

	candidates: list[str] = []
	for line in text.splitlines():
		if "=" in line:
			_, value = line.split("=", 1)
			candidates.append(value.strip().strip("\"'"))
		else:
			candidates.append(line.strip())

	for raw in candidates:
		value = _clean(raw).replace("Z", "+00:00")
		if not value:
			continue
		try:
			parsed = datetime.fromisoformat(value)
			if parsed.tzinfo is None:
				parsed = parsed.replace(tzinfo=UTC)
			return parsed.astimezone(UTC)
		except Exception:
			pass
		for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
			try:
				parsed = datetime.strptime(raw, fmt)
				if parsed.tzinfo is None:
					parsed = parsed.replace(tzinfo=UTC)
				return parsed.astimezone(UTC)
			except Exception:
				pass
	return None


def _read_metadata(backup_dir: Path) -> dict[str, str]:
	path = backup_dir / "metadata.env"
	if not path.exists():
		return {}
	try:
		return _parse_env_file(str(path))
	except Exception:
		return {}


def _list_backup_dirs(backup_root: str) -> list[Path]:
	root = Path(backup_root)
	try:
		return sorted(
			[
				path
				for path in root.iterdir()
				if path.is_dir() and (path / "metadata.env").is_file() and (path / "frappe").is_dir()
			],
			key=lambda path: path.stat().st_mtime,
			reverse=True,
		)
	except Exception:
		return []


def _age_hours(created_at: datetime | None, now: datetime) -> int | None:
	if created_at is None:
		return None
	try:
		return max(0, int((now.astimezone(UTC) - created_at.astimezone(UTC)).total_seconds() // 3600))
	except Exception:
		return None


def _env_check(env: Mapping[str, str], env_error: str | None, env_path: str | None) -> dict[str, Any]:
	missing = [key for key in REQUIRED_BACKUP_ENV_KEYS if not _clean(env.get(key))]
	if env_error:
		status = "unhealthy"
		message = "Backup environment file is missing or unreadable."
	elif missing:
		status = "unhealthy"
		message = "Backup environment is missing required keys."
	else:
		status = "healthy"
		message = "Backup environment has the required keys."

	return {
		"name": "backup_environment",
		"category": "backup_configuration",
		"status": status,
		"message": message,
		"details": {
			"env_file_configured": bool(_clean(env_path) or env),
			"env_file_state": env_error or "loaded",
			"required_key_count": len(REQUIRED_BACKUP_ENV_KEYS),
			"missing_keys": _safe_keys(missing),
		},
	}


def _script_check(env: Mapping[str, str]) -> dict[str, Any]:
	repo_root = Path(_clean(env.get("AOS_REPO_ROOT")))
	missing: list[str] = []
	not_executable: list[str] = []
	unreadable_helpers: list[str] = []
	for rel_path in BACKUP_SCRIPT_PATHS:
		path = repo_root / rel_path
		if not _file_exists(path):
			missing.append(rel_path)
		elif rel_path in BACKUP_READABLE_HELPERS:
			if not os.access(path, os.R_OK):
				unreadable_helpers.append(rel_path)
		elif not _is_executable_file(path):
			not_executable.append(rel_path)

	if missing:
		status = "unhealthy"
		message = "Required backup/restore scripts are missing."
	elif not_executable or unreadable_helpers:
		status = "degraded"
		message = "Backup entrypoints must execute and imported Python helpers must be readable."
	else:
		status = "healthy"
		message = "Backup and restore scripts are present."

	return {
		"name": "backup_restore_tooling",
		"category": "backup_tooling",
		"status": status,
		"message": message,
		"details": {
			"required_script_count": len(BACKUP_SCRIPT_PATHS),
			"missing_scripts": missing,
			"not_executable_scripts": not_executable,
			"unreadable_helpers": unreadable_helpers,
		},
	}


def _scope_checks(env: Mapping[str, str]) -> list[dict[str, Any]]:
	checks: list[dict[str, Any]] = []
	bench_root = Path(_clean(env.get("FRAPPE_BENCH_ROOT")))
	site = _clean(env.get("FRAPPE_SITE"))
	site_root = bench_root / "sites" / site if bench_root and site else Path("")

	missing_site_items: list[str] = []
	if not _dir_exists(bench_root):
		missing_site_items.append("frappe_bench_root")
	if site and not _dir_exists(site_root):
		missing_site_items.append("frappe_site_root")
	if site and not _file_exists(site_root / "site_config.json"):
		missing_site_items.append("site_config_json")

	_check(
		checks,
		name="frappe_backup_scope",
		category="backup_scope",
		status="healthy" if not missing_site_items else "unhealthy",
		message=(
			"Frappe database, public files, private files, and site config are in backup scope."
			if not missing_site_items
			else "Frappe site paths needed for database/files backup are missing."
		),
		details={
			"database_backup_expected": True,
			"public_files_backup_expected": True,
			"private_files_backup_expected": True,
			"missing_site_items": missing_site_items,
		},
	)

	include_minio = _bool_value(env.get("INCLUDE_MINIO_DATA"), True)
	external_minio = _bool_value(env.get("MINIO_EXTERNAL_BACKUP_VERIFIED"), False)
	_check(
		checks,
		name="minio_backup_scope",
		category="backup_scope",
		status="healthy" if include_minio or external_minio else "unhealthy",
		message=(
			"MinIO object data is covered by local volume backup or an external verified backup."
			if include_minio or external_minio
			else "MinIO object data is not covered by backup configuration."
		),
		details={
			"local_minio_volume_backup_enabled": include_minio,
			"external_minio_backup_verified": external_minio,
		},
	)

	include_config = _bool_value(env.get("INCLUDE_CONFIGURATION"), True)
	_check(
		checks,
		name="configuration_backup_scope",
		category="backup_scope",
		status="healthy" if include_config else "unhealthy",
		message=(
			"Site configuration and deployment environment files are in backup scope."
			if include_config
			else "Configuration backup is disabled."
		),
		details={"configuration_backup_enabled": include_config},
	)

	return checks


def _list_encrypted_backup_artifacts(env: Mapping[str, str]) -> list[dict[str, Any]]:
	backup_root = _clean(env.get("BACKUP_ROOT")) or "/var/backups/aos"
	encrypted_root = Path(_clean(env.get("ENCRYPTED_BACKUP_ROOT")) or str(Path(backup_root) / "encrypted"))
	records: list[dict[str, Any]] = []
	try:
		for artifact in encrypted_root.glob("*.tar.gz.age"):
			metadata_path = Path(str(artifact) + ".metadata.env")
			metadata = _read_marker_env(metadata_path)
			backup_id = _clean(metadata.get("BACKUP_ID")) or artifact.name.removesuffix(".tar.gz.age")
			created = _parse_backup_created_at(metadata, encrypted_root)
			if created is None:
				created = datetime.fromtimestamp(artifact.stat().st_mtime, tz=UTC)
			records.append({
				"kind": "encrypted",
				"id": backup_id,
				"path": artifact,
				"metadata_path": metadata_path,
				"metadata": metadata,
				"created_at": created,
			})
	except Exception:
		return []
	return sorted(records, key=lambda item: item["created_at"], reverse=True)


def _latest_backup_record(env: Mapping[str, str]) -> dict[str, Any] | None:
	records = _list_encrypted_backup_artifacts(env)
	backup_root = _clean(env.get("BACKUP_ROOT"))
	for backup_dir in _list_backup_dirs(backup_root) if backup_root else []:
		metadata = _read_metadata(backup_dir)
		created = _parse_backup_created_at(metadata, backup_dir)
		if created is None:
			continue
		records.append({
			"kind": "plaintext",
			"id": backup_dir.name,
			"path": backup_dir,
			"metadata_path": backup_dir / "metadata.env",
			"metadata": metadata,
			"created_at": created,
		})
	if not records:
		return None
	return sorted(records, key=lambda item: item["created_at"], reverse=True)[0]


def _latest_backup_name(env: Mapping[str, str]) -> str | None:
	record = _latest_backup_record(env)
	return str(record["id"]) if record else None

def _is_placeholder(value: Any) -> bool:
	text = _clean(value).lower()
	return any(
		token in text for token in ("placeholder", "change-me", "changeme", "example", "replace-me", "your-")
	)


def _sha256_file(path: Path) -> str | None:
	try:
		digest = hashlib.sha256()
		with path.open("rb") as handle:
			for chunk in iter(lambda: handle.read(1024 * 1024), b""):
				digest.update(chunk)
		return digest.hexdigest()
	except Exception:
		return None


def _encryption_check(env: Mapping[str, str]) -> dict[str, Any]:
	production = _clean(env.get("AOS_ENVIRONMENT") or env.get("ENVIRONMENT")).lower() == "production"
	required = production or _bool_value(env.get("BACKUP_ENCRYPTION_REQUIRED"), False)
	method = _clean(env.get("BACKUP_ENCRYPTION_METHOD") or "none").lower()
	local_mode = _clean(env.get("BACKUP_LOCAL_RETENTION_MODE") or ("encrypted-artifact" if production else "plaintext-development")).lower()
	recipient = _clean(env.get("BACKUP_AGE_RECIPIENT"))
	identity = _clean(env.get("BACKUP_AGE_IDENTITY_FILE"))
	age_binary = _clean(env.get("AGE_BINARY")) or "age"
	record = _latest_backup_record(env)
	encrypted_artifact = record.get("path") if record and record.get("kind") == "encrypted" else None
	checksum_path = Path(str(encrypted_artifact) + ".sha256") if encrypted_artifact else None
	metadata = dict(record.get("metadata") or {}) if record else {}

	recipient_valid = bool(recipient) and not _is_placeholder(recipient) and bool(re.fullmatch(r"age1[0-9a-z]{30,}", recipient))
	identity_valid = bool(identity) and not _is_placeholder(identity) and Path(identity).is_file()
	binary_available = shutil.which(age_binary) is not None
	artifact_present = bool(encrypted_artifact and Path(encrypted_artifact).is_file())
	checksum_verified = False
	if artifact_present and checksum_path and checksum_path.is_file():
		try:
			expected = checksum_path.read_text(encoding="utf-8").split()[0]
		except Exception:
			expected = ""
		checksum_verified = bool(expected) and expected == _sha256_file(Path(encrypted_artifact))

	backup_root = Path(_clean(env.get("BACKUP_ROOT")) or "/var/backups/aos")
	plaintext_dirs = _list_backup_dirs(str(backup_root))
	plaintext_work_root = Path(_clean(env.get("PLAINTEXT_WORK_ROOT")) or str(backup_root / ".plaintext-work"))
	try:
		plaintext_work_remaining = any(plaintext_work_root.iterdir()) if plaintext_work_root.is_dir() else False
	except Exception:
		plaintext_work_remaining = True
	plaintext_remaining = bool(plaintext_dirs) or plaintext_work_remaining
	metadata_plaintext_retained = _bool_value(metadata.get("PLAINTEXT_LOCAL_RETAINED"), True) if metadata else None

	errors: list[str] = []
	if method not in {"none", "age"}:
		errors.append("unsupported_method")
	if required and method != "age":
		errors.append("production_encryption_disabled")
	if production and local_mode != "encrypted-artifact":
		errors.append("unsafe_local_retention_mode")
	if method == "age" and not recipient_valid:
		errors.append("invalid_age_recipient")
	if method == "age" and not binary_available:
		errors.append("age_binary_unavailable")
	if required and not artifact_present:
		errors.append("latest_encrypted_artifact_missing")
	if artifact_present and not checksum_verified:
		errors.append("encrypted_artifact_integrity_unverified")
	if production and plaintext_remaining:
		errors.append("plaintext_backup_artifacts_remaining")
	if production and metadata_plaintext_retained is not False:
		errors.append("encrypted_artifact_missing_plaintext_cleanup_evidence")

	status = "healthy" if not errors else "unhealthy"
	message = (
		"Age encryption is configured, the latest local artifact is verified, and no plaintext backup set remains."
		if not errors
		else "Backup encryption or encrypted local-retention requirements are not satisfied."
	)
	return {
		"name": "backup_encryption",
		"category": "backup_security",
		"status": status,
		"message": message,
		"details": {
			"encryption_required": required,
			"encryption_method": method,
			"local_retention_mode": local_mode,
			"age_recipient_configured": recipient_valid,
			"age_identity_configured": identity_valid,
			"encryption_tool_available": binary_available,
			"latest_backup": str(record.get("id")) if record else None,
			"encrypted_artifact_present": artifact_present,
			"encrypted_artifact_checksum_verified": checksum_verified,
			"encrypted_artifact_integrity_verified": checksum_verified,
			"local_encrypted_retention_status": artifact_present and checksum_verified and not plaintext_remaining,
			"plaintext_artifact_remaining": plaintext_remaining,
			"plaintext_cleanup_evidence": metadata_plaintext_retained is False,
			"errors": errors,
		},
	}

def _offsite_mode(env: Mapping[str, str]) -> str:
	mode = _clean(env.get("OFFSITE_BACKUP_MODE")).lower()
	if mode:
		return mode
	if _clean(env.get("REMOTE_COPY_COMMAND")):
		return "custom"
	if _bool_value(env.get("OFFSITE_BACKUP_ENABLED"), False):
		return "rsync"
	if _bool_value(env.get("OFFSITE_BACKUP_CONFIGURED"), False):
		return "external"
	return "disabled"


def _offsite_marker_path(env: Mapping[str, str]) -> Path:
	marker = _clean(env.get("OFFSITE_SYNC_MARKER"))
	if marker:
		return Path(marker)
	backup_root = _clean(env.get("BACKUP_ROOT")) or "/var/backups/aos"
	return Path(backup_root) / "offsite-sync-passed.env"


def _offsite_config_missing(env: Mapping[str, str], mode: str) -> list[str]:
	if mode in {"disabled", "none"}:
		return []
	if mode == "rsync":
		return [] if _clean(env.get("OFFSITE_RSYNC_TARGET")) else ["OFFSITE_RSYNC_TARGET"]
	if mode == "s3":
		missing = []
		if not _clean(env.get("OFFSITE_S3_BUCKET")):
			missing.append("OFFSITE_S3_BUCKET")
		return missing
	if mode == "custom":
		return [] if _clean(env.get("REMOTE_COPY_COMMAND")) else ["REMOTE_COPY_COMMAND"]
	if mode == "external":
		return []
	return ["OFFSITE_BACKUP_MODE"]


def _read_marker_env(path: Path) -> dict[str, str]:
	if not path.exists():
		return {}
	try:
		return _parse_env_file(str(path))
	except Exception:
		return {}


def _offsite_backup_check(
	env: Mapping[str, str], *, now: datetime, max_sync_age_hours: int
) -> dict[str, Any]:
	mode = _offsite_mode(env)
	configured = mode not in {"disabled", "none"}
	latest_record = _latest_backup_record(env)
	latest_backup = _clean((latest_record or {}).get("id"))
	latest_artifact = (latest_record or {}).get("path")
	expected_artifact_name = latest_artifact.name if isinstance(latest_artifact, Path) and (latest_record or {}).get("kind") == "encrypted" else ""
	expected_artifact_checksum = ""
	if expected_artifact_name and isinstance(latest_artifact, Path):
		checksum_path = Path(str(latest_artifact) + ".sha256")
		try:
			expected_artifact_checksum = checksum_path.read_text(encoding="utf-8").split()[0]
		except Exception:
			expected_artifact_checksum = ""
	marker_path = _offsite_marker_path(env)
	marker_env = _read_marker_env(marker_path)
	marker_time = _parse_marker_time(marker_path) if marker_path.exists() else None
	marker_age_hours = _age_hours(marker_time, now)
	marker_backup_id = _clean(marker_env.get("OFFSITE_BACKUP_ID"))
	marker_mode = _clean(marker_env.get("OFFSITE_BACKUP_MODE"))
	marker_encryption = _clean(marker_env.get("OFFSITE_ENCRYPTION_METHOD")).lower()
	marker_artifact_kind = _clean(marker_env.get("OFFSITE_ARTIFACT_KIND")).lower()
	marker_artifact_name = _clean(marker_env.get("OFFSITE_ARTIFACT_NAME"))
	marker_artifact_checksum = _clean(marker_env.get("OFFSITE_ENCRYPTED_SHA256")).lower()
	marker_artifact_matches_latest = bool(expected_artifact_name and marker_artifact_name == expected_artifact_name)
	marker_checksum_matches_latest = bool(
		expected_artifact_checksum
		and marker_artifact_checksum
		and hmac.compare_digest(marker_artifact_checksum, expected_artifact_checksum)
	)
	encryption_required = _clean(
		env.get("AOS_ENVIRONMENT") or env.get("ENVIRONMENT")
	).lower() == "production" or _bool_value(env.get("BACKUP_ENCRYPTION_REQUIRED"), False)
	marker_backup_matches_latest = bool(
		latest_backup and marker_backup_id and marker_backup_id == latest_backup
	)
	missing = _offsite_config_missing(env, mode)

	if not configured:
		status = "degraded"
		message = "No off-server backup copy mechanism is configured."
	elif missing:
		status = "unhealthy"
		message = "Offsite backup is enabled but required configuration is missing."
	elif not marker_path.exists():
		status = "unhealthy"
		message = "Offsite backup is configured but no sync marker was found."
	elif marker_age_hours is None or marker_age_hours > max_sync_age_hours:
		status = "unhealthy"
		message = "Offsite backup sync marker is stale or missing a valid timestamp."
	elif latest_backup and marker_backup_id and marker_backup_id != latest_backup:
		status = "unhealthy"
		message = "Latest local backup has not been confirmed offsite."
	elif encryption_required and marker_encryption != "age":
		status = "unhealthy"
		message = "Offsite backup marker does not prove production-required encryption."
	elif encryption_required and marker_artifact_kind != "encrypted-age":
		status = "unhealthy"
		message = "Offsite backup marker does not prove an encrypted artifact was copied."
	elif encryption_required and expected_artifact_name and not marker_artifact_matches_latest:
		status = "unhealthy"
		message = "Offsite backup marker refers to another encrypted artifact."
	elif encryption_required and expected_artifact_checksum and not marker_checksum_matches_latest:
		status = "unhealthy"
		message = "Offsite backup marker does not match the encrypted artifact checksum."
	else:
		status = "healthy"
		message = "A recent off-server backup copy has been verified."

	return {
		"name": "offsite_backup_scope",
		"category": "backup_scope",
		"status": status,
		"message": message,
		"details": {
			"offsite_backup_configured": configured,
			"offsite_backup_mode": mode if mode in {"rsync", "s3", "custom", "external"} else "disabled",
			"missing_keys": _safe_keys(missing),
			"sync_marker_present": marker_path.exists(),
			"sync_marker_age_hours": marker_age_hours,
			"max_sync_age_hours": max_sync_age_hours,
			"latest_backup": latest_backup,
			"marker_backup_matches_latest": marker_backup_matches_latest,
			"marker_mode": marker_mode
			if marker_mode in {"rsync", "s3", "custom", "external"}
			else (marker_mode or None),
			"marker_encryption_method": marker_encryption or None,
			"marker_artifact_matches_latest": marker_artifact_matches_latest,
			"marker_checksum_matches_latest": marker_checksum_matches_latest,
			"production_encryption_required": encryption_required,
		},
	}


def _latest_backup_check(
	env: Mapping[str, str], *, now: datetime, max_backup_age_hours: int
) -> dict[str, Any]:
	record = _latest_backup_record(env)
	if not record:
		return {
			"name": "latest_backup_artifact",
			"category": "backup_artifact",
			"status": "unhealthy",
			"message": "No backup artifacts were found.",
			"details": {"backup_count": 0},
		}
	created_at = record.get("created_at")
	age = _age_hours(created_at, now)
	metadata = dict(record.get("metadata") or {})
	kind = str(record.get("kind"))
	missing: list[str] = []
	if kind == "encrypted":
		artifact = Path(record["path"])
		checksum = Path(str(artifact) + ".sha256")
		has_database = _bool_value(metadata.get("DATABASE_BACKUP_PRESENT"), False)
		has_public = _bool_value(metadata.get("PUBLIC_FILES_BACKUP_PRESENT"), False)
		has_private = _bool_value(metadata.get("PRIVATE_FILES_BACKUP_PRESENT"), False)
		has_checksums = checksum.is_file() and _sha256_file(artifact) == (checksum.read_text(encoding="utf-8").split()[0] if checksum.is_file() else "")
		verified = _bool_value(metadata.get("ENCRYPTED_ARTIFACT_VERIFIED"), False) and has_checksums
		has_minio = _bool_value(metadata.get("MINIO_ARCHIVE_PRESENT"), False)
		has_config = _bool_value(metadata.get("CONFIGURATION_SNAPSHOT_PRESENT"), False)
	else:
		latest = Path(record["path"])
		frappe_dir = latest / "frappe"
		names = [path.name for path in frappe_dir.iterdir()] if frappe_dir.is_dir() else []
		has_database = any("database.sql" in name for name in names)
		has_private = any(re.search(r"(?:^|-)private-files\.(?:tgz|tar|tar\.gz)$", name) for name in names)
		has_public = any("private-files" not in name and re.search(r"(?:^|-)files\.(?:tgz|tar|tar\.gz)$", name) for name in names)
		has_checksums = (latest / "SHA256SUMS").is_file()
		verified = (latest / "VERIFIED_AT_UTC").is_file()
		has_minio = (latest / "docker" / "minio_data.tar.gz").is_file()
		has_config = (latest / "config" / "site_config.json").is_file() and (latest / "config" / "aos.env").is_file()
	for present, item in ((has_database, "database_backup"), (has_public, "public_files_backup"), (has_private, "private_files_backup"), (has_checksums, "checksum_evidence"), (verified, "verification_evidence")):
		if not present:
			missing.append(item)
	if _bool_value(env.get("INCLUDE_MINIO_DATA"), True) and not _bool_value(env.get("MINIO_EXTERNAL_BACKUP_VERIFIED"), False) and not has_minio:
		missing.append("minio_data_archive")
	if _bool_value(env.get("INCLUDE_CONFIGURATION"), True) and not has_config:
		missing.append("configuration_snapshot")
	is_stale = age is None or age > max_backup_age_hours
	status = "healthy" if not missing and not is_stale else "unhealthy"
	return {
		"name": "latest_backup_artifact",
		"category": "backup_artifact",
		"status": status,
		"message": "Latest backup artifact is recent and complete." if status == "healthy" else "Latest backup artifact is missing required evidence or is too old.",
		"details": {
			"backup_count": len(_list_encrypted_backup_artifacts(env)) + len(_list_backup_dirs(_clean(env.get("BACKUP_ROOT")))),
			"latest_backup": str(record["id"]),
			"artifact_kind": kind,
			"latest_backup_age_hours": age,
			"max_backup_age_hours": max_backup_age_hours,
			"has_database_backup": has_database,
			"has_public_files_backup": has_public,
			"has_private_files_backup": has_private,
			"has_minio_archive": has_minio or _bool_value(env.get("MINIO_EXTERNAL_BACKUP_VERIFIED"), False),
			"has_configuration_snapshot": has_config,
			"has_checksums": has_checksums,
			"has_verification_marker": verified,
			"missing_items": missing,
		},
	}

def _restore_rehearsal_check(env: Mapping[str, str], *, now: datetime, max_age_days: int) -> dict[str, Any]:
	marker_value = _clean(env.get("RESTORE_REHEARSAL_MARKER"))
	backup_root = _clean(env.get("BACKUP_ROOT")) or "/var/backups/aos"
	marker = Path(marker_value) if marker_value else Path(backup_root) / "restore-rehearsal-passed.env"
	latest = _latest_backup_record(env)
	latest_backup = str(latest["id"]) if latest else None
	if not marker.exists():
		return {
			"name": "restore_rehearsal",
			"category": "restore_verification",
			"status": "unhealthy",
			"message": "No restore rehearsal marker was found.",
			"details": {"restore_rehearsal_marker_present": False, "max_age_days": max_age_days},
		}

	marker_env = _read_marker_env(marker)
	passed_at = _parse_marker_time(marker)
	restore_completed = None
	try:
		restore_completed = datetime.fromisoformat(_clean(marker_env.get("RESTORE_COMPLETED_AT_UTC")).replace("Z", "+00:00"))
		if restore_completed.tzinfo is None:
			restore_completed = restore_completed.replace(tzinfo=UTC)
	except Exception:
		restore_completed = None
	age_days = None
	if passed_at is not None:
		age_days = max(0, int((now.astimezone(UTC) - passed_at.astimezone(UTC)).total_seconds() // 86400))

	marker_backup = _clean(marker_env.get("BACKUP_ID") or marker_env.get("BACKUP_BASENAME"))
	mode = _clean(marker_env.get("RESTORE_REHEARSAL_MODE") or "full").lower()
	require_files = _bool_value(env.get("RESTORE_REHEARSAL_REQUIRE_FILES"), _clean(env.get("AOS_ENVIRONMENT") or env.get("ENVIRONMENT")).lower() == "production")
	public_present = _bool_value(marker_env.get("PUBLIC_ARCHIVE_PRESENT") or marker_env.get("PUBLIC_FILES_PRESENT"), False)
	private_present = _bool_value(marker_env.get("PRIVATE_ARCHIVE_PRESENT") or marker_env.get("PRIVATE_FILES_PRESENT"), False)
	public_result = _clean(marker_env.get("PUBLIC_FILES_RESTORE_RESULT"))
	private_result = _clean(marker_env.get("PRIVATE_FILES_RESTORE_RESULT"))
	public_verify = _clean(marker_env.get("PUBLIC_REPRESENTATIVE_FILE_VERIFY_RESULT") or marker_env.get("PUBLIC_REPRESENTATIVE_VERIFY_RESULT"))
	private_verify = _clean(marker_env.get("PRIVATE_REPRESENTATIVE_FILE_VERIFY_RESULT") or marker_env.get("PRIVATE_REPRESENTATIVE_VERIFY_RESULT"))

	errors: list[str] = []
	rehearsal_environment = _clean(marker_env.get("RESTORE_REHEARSAL_ENVIRONMENT")).lower()
	if rehearsal_environment not in {"staging", "rehearsal", "test"}:
		errors.append("invalid_or_production_rehearsal_environment")
	if age_days is None or age_days > max_age_days:
		errors.append("stale_or_invalid_timestamp")
	if latest_backup and marker_backup != latest_backup:
		errors.append("marker_backup_mismatch")
	if not (passed_at and restore_completed and passed_at >= restore_completed):
		errors.append("marker_predates_restore_completion")
	for key, expected in {
		"DATABASE_RESTORE_RESULT": "restored",
		"CHECKSUM_VERIFICATION_RESULT": "passed",
		"MIGRATION_RESULT": "passed",
		"PRODUCTION_CONFIG_RESULT": "passed",
		"HEALTH_CHECK_RESULT": "passed",
		"JOB_MONITORING_RESULT": "passed",
	}.items():
		if _clean(marker_env.get(key)) != expected:
			errors.append(key)
	if require_files:
		if mode != "full":
			errors.append("database_only_rehearsal_not_allowed")
		if not public_present:
			errors.append("public_archive_missing")
		if not private_present:
			errors.append("private_archive_missing")
		if public_result != "checksum_matched" or public_verify != "passed":
			errors.append("public_file_restore_evidence")
		if private_result != "checksum_matched" or private_verify != "passed":
			errors.append("private_file_restore_evidence")
	elif mode == "database-only" and not _bool_value(env.get("RESTORE_REHEARSAL_ALLOW_DATABASE_ONLY"), False):
		errors.append("database_only_rehearsal_not_explicitly_allowed")

	status = "healthy" if not errors else "unhealthy"
	return {
		"name": "restore_rehearsal",
		"category": "restore_verification",
		"status": status,
		"message": "A recent full restore rehearsal has verified the latest backup." if status == "healthy" else "Restore rehearsal evidence is stale, incomplete, database-only under a full policy, or refers to another backup.",
		"details": {
			"restore_rehearsal_marker_present": True,
			"restore_rehearsal_age_days": age_days,
			"max_age_days": max_age_days,
			"latest_backup": latest_backup,
			"marker_backup_matches_latest": bool(latest_backup and marker_backup == latest_backup),
			"marker_after_restore_completion": bool(passed_at and restore_completed and passed_at >= restore_completed),
			"rehearsal_environment": rehearsal_environment or None,
			"rehearsal_mode": mode,
			"full_file_restore_required": require_files,
			"public_archive_present": public_present,
			"public_files_restore_verified": public_result == "checksum_matched" and public_verify == "passed",
			"private_archive_present": private_present,
			"private_files_restore_verified": private_result == "checksum_matched" and private_verify == "passed",
			"checksum_verification_passed": _clean(marker_env.get("CHECKSUM_VERIFICATION_RESULT")) == "passed",
			"errors": errors,
		},
	}

def validate_backup_readiness(
	*,
	backup_env: Mapping[str, Any] | None = None,
	backup_env_path: str | None = None,
	max_backup_age_hours: int = DEFAULT_MAX_BACKUP_AGE_HOURS,
	restore_rehearsal_max_age_days: int = DEFAULT_RESTORE_REHEARSAL_MAX_AGE_DAYS,
	offsite_max_sync_age_hours: int = DEFAULT_OFFSITE_MAX_SYNC_AGE_HOURS,
	now: datetime | None = None,
) -> dict[str, Any]:
	"""Return a redacted backup/restore readiness report for AOS."""

	env, env_path, env_error = _resolve_backup_env(backup_env=backup_env, backup_env_path=backup_env_path)
	current_time = now or now_datetime()
	if current_time.tzinfo is None:
		current_time = current_time.replace(tzinfo=UTC)
	else:
		current_time = current_time.astimezone(UTC)

	max_age_hours = max(1, min(cint(max_backup_age_hours) or DEFAULT_MAX_BACKUP_AGE_HOURS, 24 * 14))
	max_rehearsal_days = max(
		1, min(cint(restore_rehearsal_max_age_days) or DEFAULT_RESTORE_REHEARSAL_MAX_AGE_DAYS, 365)
	)
	configured_offsite_age = _safe_int(env.get("OFFSITE_MAX_SYNC_AGE_HOURS"), offsite_max_sync_age_hours)
	max_offsite_hours = max(1, min(configured_offsite_age or DEFAULT_OFFSITE_MAX_SYNC_AGE_HOURS, 24 * 30))

	checks: list[dict[str, Any]] = []
	checks.append(_env_check(env, env_error, env_path))

	if not env_error:
		checks.append(_script_check(env))
		checks.extend(_scope_checks(env))
		checks.append(_encryption_check(env))
		checks.append(_latest_backup_check(env, now=current_time, max_backup_age_hours=max_age_hours))
		checks.append(_offsite_backup_check(env, now=current_time, max_sync_age_hours=max_offsite_hours))
		checks.append(_restore_rehearsal_check(env, now=current_time, max_age_days=max_rehearsal_days))

	counts = {
		"healthy": 0,
		"degraded": 0,
		"unhealthy": 0,
		"skipped": 0,
		"checks": len(checks),
	}
	for check in checks:
		status = _clean(check.get("status")) or "unhealthy"
		if status not in counts:
			status = "unhealthy"
		counts[status] += 1

	return {
		"ready": counts["unhealthy"] == 0,
		"summary": counts,
		"checks": checks,
	}


def backup_readiness_summary() -> dict[str, Any]:
	"""Bench-friendly backup/restore readiness report."""

	return validate_backup_readiness()


def assert_backup_readiness_ready() -> dict[str, Any]:
	"""Raise when encrypted backup and full rehearsal evidence are not ready."""
	report = validate_backup_readiness()
	if not report.get("ready"):
		unhealthy = int((report.get("summary") or {}).get("unhealthy") or 0)
		raise RuntimeError(f"AOS backup readiness is not ready: {unhealthy} unhealthy check(s).")
	return report
