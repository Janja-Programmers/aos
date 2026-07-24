#!/usr/bin/env python3
"""Strict, testable restore-rehearsal evidence policy."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from infra.backup.backup_artifacts import discover_backup_artifacts
except ModuleNotFoundError:  # Direct execution from infra/backup.
    from backup_artifacts import discover_backup_artifacts

TRUE_VALUES = {"1", "true", "yes", "on"}


class RehearsalPolicyError(RuntimeError):
    pass


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES


def _env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        raise RehearsalPolicyError("Restore state marker is missing.")
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _timestamp(value: str, label: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception as exc:
        raise RehearsalPolicyError(f"{label} is missing or invalid.") from exc


def validate_rehearsal_evidence(
    backup_dir: Path,
    restore_state_marker: Path,
    *,
    require_files: bool,
    allow_database_only: bool,
) -> dict[str, Any]:
    backup_dir = backup_dir.resolve()
    state = _env(restore_state_marker)
    artifacts = discover_backup_artifacts(backup_dir / "frappe")
    backup_id = backup_dir.name
    if state.get("BACKUP_ID") != backup_id:
        raise RehearsalPolicyError("Restore state marker refers to another backup.")
    if state.get("DATABASE_RESTORE_RESULT") != "restored":
        raise RehearsalPolicyError("Database restoration evidence is missing.")
    if state.get("MIGRATION_RESULT") != "passed":
        raise RehearsalPolicyError("Migration evidence is missing.")
    restored_at = _timestamp(state.get("DB_RESTORED_AT_UTC", ""), "Database restore timestamp")
    completed_at = _timestamp(state.get("RESTORE_COMPLETED_AT_UTC", ""), "Restore completion timestamp")
    if completed_at < restored_at:
        raise RehearsalPolicyError("Restore completion predates database restore completion.")

    public_present = bool(artifacts.public_files)
    private_present = bool(artifacts.private_files)
    public_result = state.get("PUBLIC_FILES_RESTORE_RESULT", "")
    private_result = state.get("PRIVATE_FILES_RESTORE_RESULT", "")
    public_verify = state.get("PUBLIC_REPRESENTATIVE_VERIFY_RESULT") or state.get(
        "PUBLIC_REPRESENTATIVE_FILE_VERIFY_RESULT", ""
    )
    private_verify = state.get("PRIVATE_REPRESENTATIVE_VERIFY_RESULT") or state.get(
        "PRIVATE_REPRESENTATIVE_FILE_VERIFY_RESULT", ""
    )
    if public_present and not public_verify:
        public_verify = "passed" if public_result == "checksum_matched" else "failed"
    if private_present and not private_verify:
        private_verify = "passed" if private_result == "checksum_matched" else "failed"

    mode = "database-only" if not public_present and not private_present else "full"
    errors: list[str] = []
    if require_files:
        if not public_present:
            errors.append("public_archive_missing")
        if not private_present:
            errors.append("private_archive_missing")
        if public_result != "checksum_matched" or public_verify != "passed":
            errors.append("public_restored_file_verification_failed")
        if private_result != "checksum_matched" or private_verify != "passed":
            errors.append("private_restored_file_verification_failed")
        mode = "full"
    elif mode == "database-only" and not allow_database_only:
        errors.append("database_only_not_explicitly_allowed")
    else:
        if public_present and (public_result != "checksum_matched" or public_verify != "passed"):
            errors.append("public_restored_file_verification_failed")
        if private_present and (private_result != "checksum_matched" or private_verify != "passed"):
            errors.append("private_restored_file_verification_failed")
    if errors:
        raise RehearsalPolicyError("Restore rehearsal evidence failed: " + ", ".join(errors))

    return {
        "backup_id": backup_id,
        "mode": mode,
        "database_restore_result": "restored",
        "public_archive_present": public_present,
        "public_restore_result": public_result or "not_present",
        "public_representative_verify_result": public_verify or "not_present",
        "private_archive_present": private_present,
        "private_restore_result": private_result or "not_present",
        "private_representative_verify_result": private_verify or "not_present",
        "checksum_verification_result": state.get("CHECKSUM_VERIFICATION_RESULT", ""),
        "restore_completed_at": state.get("RESTORE_COMPLETED_AT_UTC", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--restore-state", required=True)
    parser.add_argument("--require-files", default="true")
    parser.add_argument("--allow-database-only", default="false")
    args = parser.parse_args()
    try:
        result = validate_rehearsal_evidence(
            Path(args.backup_dir),
            Path(args.restore_state),
            require_files=_truthy(args.require_files),
            allow_database_only=_truthy(args.allow_database_only),
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except RehearsalPolicyError as exc:
        print(f"restore rehearsal policy error: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
