"""Backup and restore-readiness validation for AOS production operations.

This module is intentionally read-only. It checks whether the deployment has
usable backup tooling, backup configuration, a recent verified backup artifact,
and evidence of a recent restore rehearsal without exposing environment values,
secrets, site-config contents, private file paths, database credentials, or raw
backup file contents.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frappe
from frappe.utils import cint, now_datetime

BackupStatus = str

DEFAULT_BACKUP_ENV_FILE = "/etc/aos/backup.env"
DEFAULT_MAX_BACKUP_AGE_HOURS = 26
DEFAULT_RESTORE_REHEARSAL_MAX_AGE_DAYS = 30

REQUIRED_BACKUP_ENV_KEYS: tuple[str, ...] = (
    "AOS_REPO_ROOT",
    "FRAPPE_BENCH_ROOT",
    "FRAPPE_SITE",
    "BACKUP_ROOT",
)

BACKUP_SCRIPT_PATHS: tuple[str, ...] = (
    "infra/backup/backup.sh",
    "infra/backup/restore.sh",
    "infra/backup/verify-backup.sh",
    "infra/backup/restore-rehearsal-checklist.sh",
)

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
    with open(path, "r", encoding="utf-8") as handle:
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
                return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            except Exception:
                pass
    try:
        return datetime.fromtimestamp(backup_dir.stat().st_mtime, tz=timezone.utc)
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
            candidates.append(value.strip().strip('"\''))
        else:
            candidates.append(line.strip())

    for raw in candidates:
        value = _clean(raw).replace("Z", "+00:00")
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            pass
        for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(raw, fmt)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
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
            [path for path in root.iterdir() if path.is_dir()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return []


def _age_hours(created_at: datetime | None, now: datetime) -> int | None:
    if created_at is None:
        return None
    try:
        return max(0, int((now.astimezone(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds() // 3600))
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
    for rel_path in BACKUP_SCRIPT_PATHS:
        path = repo_root / rel_path
        if not _file_exists(path):
            missing.append(rel_path)
        elif not _is_executable_file(path):
            not_executable.append(rel_path)

    if missing:
        status = "unhealthy"
        message = "Required backup/restore scripts are missing."
    elif not_executable:
        status = "degraded"
        message = "Some backup/restore scripts are not executable."
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

    remote_copy = bool(_clean(env.get("REMOTE_COPY_COMMAND"))) or _bool_value(env.get("OFFSITE_BACKUP_CONFIGURED"), False)
    _check(
        checks,
        name="offsite_backup_scope",
        category="backup_scope",
        status="healthy" if remote_copy else "degraded",
        message=(
            "An off-server backup copy mechanism is configured."
            if remote_copy
            else "No off-server backup copy mechanism is configured."
        ),
        details={"offsite_backup_configured": remote_copy},
    )
    return checks


def _latest_backup_check(env: Mapping[str, str], *, now: datetime, max_backup_age_hours: int) -> dict[str, Any]:
    backup_root = _clean(env.get("BACKUP_ROOT"))
    if not backup_root:
        return {
            "name": "latest_backup_artifact",
            "category": "backup_artifact",
            "status": "unhealthy",
            "message": "Backup root is not configured.",
            "details": {"backup_root_configured": False},
        }

    backup_dirs = _list_backup_dirs(backup_root)
    if not backup_dirs:
        return {
            "name": "latest_backup_artifact",
            "category": "backup_artifact",
            "status": "unhealthy",
            "message": "No backup artifacts were found.",
            "details": {"backup_root_configured": True, "backup_count": 0},
        }

    latest = backup_dirs[0]
    metadata = _read_metadata(latest)
    created_at = _parse_backup_created_at(metadata, latest)
    age = _age_hours(created_at, now)

    frappe_dir = latest / "frappe"
    docker_dir = latest / "docker"
    config_dir = latest / "config"

    has_database = bool(list(frappe_dir.glob("*database.sql*")))
    # Frappe's native backup uses .tgz for file archives, while some
    # older/manual backup layouts may use .tar or .tar.gz. Accept all of
    # those forms so readiness matches the artifact created by backup.sh.
    public_file_archives = [
        path
        for path in frappe_dir.iterdir()
        if path.is_file()
        and "private-files" not in path.name
        and re.search(r"(?:^|-)files\.(?:tgz|tar|tar\.gz)$", path.name)
    ]
    private_file_archives = [
        path
        for path in frappe_dir.iterdir()
        if path.is_file() and re.search(r"(?:^|-)private-files\.(?:tgz|tar|tar\.gz)$", path.name)
    ]
    has_public_files = bool(public_file_archives)
    has_private_files = bool(private_file_archives)
    has_checksums = _file_exists(latest / "SHA256SUMS")
    has_metadata = _file_exists(latest / "metadata.env")
    has_verification_marker = _file_exists(latest / "VERIFIED_AT_UTC")
    include_minio = _bool_value(env.get("INCLUDE_MINIO_DATA"), True)
    external_minio = _bool_value(env.get("MINIO_EXTERNAL_BACKUP_VERIFIED"), False)
    has_minio_archive = _file_exists(docker_dir / "minio_data.tar.gz")
    include_config = _bool_value(env.get("INCLUDE_CONFIGURATION"), True)
    has_config_snapshot = _file_exists(config_dir / "site_config.json") and _file_exists(config_dir / "aos.env")

    missing: list[str] = []
    if not has_metadata:
        missing.append("metadata.env")
    if not has_checksums:
        missing.append("SHA256SUMS")
    if not has_database:
        missing.append("database_backup")
    if not has_public_files:
        missing.append("public_files_backup")
    if not has_private_files:
        missing.append("private_files_backup")
    if include_minio and not external_minio and not has_minio_archive:
        missing.append("minio_data_archive")
    if include_config and not has_config_snapshot:
        missing.append("configuration_snapshot")

    is_stale = age is None or age > max_backup_age_hours
    if missing or is_stale:
        status = "unhealthy"
        message = "Latest backup artifact is missing required parts or is too old."
    elif not has_verification_marker:
        status = "degraded"
        message = "Latest backup artifact looks complete but has no verification marker."
    else:
        status = "healthy"
        message = "Latest backup artifact is recent and complete."

    return {
        "name": "latest_backup_artifact",
        "category": "backup_artifact",
        "status": status,
        "message": message,
        "details": {
            "backup_root_configured": True,
            "backup_count": len(backup_dirs),
            "latest_backup": latest.name,
            "latest_backup_age_hours": age,
            "max_backup_age_hours": max_backup_age_hours,
            "has_database_backup": has_database,
            "has_public_files_backup": has_public_files,
            "has_private_files_backup": has_private_files,
            "has_minio_archive": has_minio_archive or external_minio,
            "has_configuration_snapshot": has_config_snapshot,
            "has_checksums": has_checksums,
            "has_verification_marker": has_verification_marker,
            "missing_items": missing,
        },
    }


def _restore_rehearsal_check(env: Mapping[str, str], *, now: datetime, max_age_days: int) -> dict[str, Any]:
    marker_value = _clean(env.get("RESTORE_REHEARSAL_MARKER"))
    if marker_value:
        marker = Path(marker_value)
    else:
        backup_root = _clean(env.get("BACKUP_ROOT")) or "/var/backups/aos"
        marker = Path(backup_root) / "restore-rehearsal-passed.env"

    if not marker.exists():
        return {
            "name": "restore_rehearsal",
            "category": "restore_verification",
            "status": "unhealthy",
            "message": "No restore rehearsal marker was found.",
            "details": {
                "restore_rehearsal_marker_present": False,
                "max_age_days": max_age_days,
            },
        }

    passed_at = _parse_marker_time(marker)
    age_days: int | None = None
    if passed_at is not None:
        try:
            age_days = max(0, int((now.astimezone(timezone.utc) - passed_at.astimezone(timezone.utc)).total_seconds() // 86400))
        except Exception:
            age_days = None

    if age_days is None or age_days > max_age_days:
        status = "unhealthy"
        message = "Restore rehearsal marker is missing a fresh verification timestamp."
    else:
        status = "healthy"
        message = "A recent restore rehearsal has been recorded."

    return {
        "name": "restore_rehearsal",
        "category": "restore_verification",
        "status": status,
        "message": message,
        "details": {
            "restore_rehearsal_marker_present": True,
            "restore_rehearsal_age_days": age_days,
            "max_age_days": max_age_days,
        },
    }


def validate_backup_readiness(
    *,
    backup_env: Mapping[str, Any] | None = None,
    backup_env_path: str | None = None,
    max_backup_age_hours: int = DEFAULT_MAX_BACKUP_AGE_HOURS,
    restore_rehearsal_max_age_days: int = DEFAULT_RESTORE_REHEARSAL_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a redacted backup/restore readiness report for AOS."""

    env, env_path, env_error = _resolve_backup_env(backup_env=backup_env, backup_env_path=backup_env_path)
    current_time = now or now_datetime()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    else:
        current_time = current_time.astimezone(timezone.utc)

    max_age_hours = max(1, min(cint(max_backup_age_hours) or DEFAULT_MAX_BACKUP_AGE_HOURS, 24 * 14))
    max_rehearsal_days = max(1, min(cint(restore_rehearsal_max_age_days) or DEFAULT_RESTORE_REHEARSAL_MAX_AGE_DAYS, 365))

    checks: list[dict[str, Any]] = []
    checks.append(_env_check(env, env_error, env_path))

    if not env_error:
        checks.append(_script_check(env))
        checks.extend(_scope_checks(env))
        checks.append(_latest_backup_check(env, now=current_time, max_backup_age_hours=max_age_hours))
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
