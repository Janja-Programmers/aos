from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from infra.backup.rehearsal_policy import RehearsalPolicyError, validate_rehearsal_evidence


def _tar(path: Path, member: str, content: bytes = b"expected") -> None:
	with tarfile.open(path, "w:gz") as handle:
		info = tarfile.TarInfo(member)
		info.size = len(content)
		handle.addfile(info, io.BytesIO(content))


def _backup(tmp_path: Path, *, public: bool = True, private: bool = True) -> Path:
	backup = tmp_path / "20260720T120000Z"
	frappe_dir = backup / "frappe"
	frappe_dir.mkdir(parents=True)
	(frappe_dir / "x-database.sql.gz").write_bytes(b"db")
	if public:
		_tar(frappe_dir / "x-files.tgz", "files/public.txt")
	if private:
		_tar(frappe_dir / "x-private-files.tgz", "private/files/private.txt")
	return backup


def _state(backup: Path, **overrides: str) -> Path:
	values = {
		"BACKUP_ID": backup.name,
		"DATABASE_RESTORE_RESULT": "restored",
		"DB_RESTORED_AT_UTC": "2026-07-20T12:00:00Z",
		"RESTORE_COMPLETED_AT_UTC": "2026-07-20T12:10:00Z",
		"MIGRATION_RESULT": "passed",
		"CHECKSUM_VERIFICATION_RESULT": "passed",
		"PUBLIC_FILES_RESTORE_RESULT": "checksum_matched",
		"PUBLIC_REPRESENTATIVE_VERIFY_RESULT": "passed",
		"PRIVATE_FILES_RESTORE_RESULT": "checksum_matched",
		"PRIVATE_REPRESENTATIVE_VERIFY_RESULT": "passed",
	}
	values.update(overrides)
	marker = backup.parent / "restore-state.env"
	marker.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
	return marker


def test_full_successful_restore(tmp_path: Path):
	backup = _backup(tmp_path)
	result = validate_rehearsal_evidence(
		backup, _state(backup), require_files=True, allow_database_only=False
	)
	assert result["mode"] == "full"
	assert result["public_representative_verify_result"] == "passed"
	assert result["private_representative_verify_result"] == "passed"


@pytest.mark.parametrize(
	("public", "private", "error"),
	[
		(False, True, "public_archive_missing"),
		(True, False, "private_archive_missing"),
		(False, False, "public_archive_missing"),
	],
)
def test_missing_archive_fails_when_files_required(tmp_path: Path, public: bool, private: bool, error: str):
	backup = _backup(tmp_path, public=public, private=private)
	with pytest.raises(RehearsalPolicyError, match=error):
		validate_rehearsal_evidence(backup, _state(backup), require_files=True, allow_database_only=False)


@pytest.mark.parametrize(
	("field", "error"),
	[
		("PUBLIC_FILES_RESTORE_RESULT", "public_restored_file_verification_failed"),
		("PUBLIC_REPRESENTATIVE_VERIFY_RESULT", "public_restored_file_verification_failed"),
		("PRIVATE_FILES_RESTORE_RESULT", "private_restored_file_verification_failed"),
		("PRIVATE_REPRESENTATIVE_VERIFY_RESULT", "private_restored_file_verification_failed"),
	],
)
def test_missing_restored_file_or_checksum_mismatch_fails(tmp_path: Path, field: str, error: str):
	backup = _backup(tmp_path)
	value = "failed"
	with pytest.raises(RehearsalPolicyError, match=error):
		validate_rehearsal_evidence(
			backup,
			_state(backup, **{field: value}),
			require_files=True,
			allow_database_only=False,
		)


def test_database_only_requires_explicit_policy(tmp_path: Path):
	backup = _backup(tmp_path, public=False, private=False)
	marker = _state(
		backup,
		PUBLIC_FILES_RESTORE_RESULT="not_present",
		PUBLIC_REPRESENTATIVE_VERIFY_RESULT="not_present",
		PRIVATE_FILES_RESTORE_RESULT="not_present",
		PRIVATE_REPRESENTATIVE_VERIFY_RESULT="not_present",
	)
	with pytest.raises(RehearsalPolicyError, match="database_only_not_explicitly_allowed"):
		validate_rehearsal_evidence(backup, marker, require_files=False, allow_database_only=False)
	result = validate_rehearsal_evidence(backup, marker, require_files=False, allow_database_only=True)
	assert result["mode"] == "database-only"
	assert result["public_restore_result"] == "not_present"


def test_marker_for_another_backup_is_rejected(tmp_path: Path):
	backup = _backup(tmp_path)
	marker = _state(backup, BACKUP_ID="another-backup")
	with pytest.raises(RehearsalPolicyError, match="another backup"):
		validate_rehearsal_evidence(backup, marker, require_files=True, allow_database_only=False)
