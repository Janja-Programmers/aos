from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from infra.backup.backup_artifacts import (
	ArtifactDiscoveryError,
	build_restore_args,
	discover_backup_artifacts,
	verify_restored_archive,
)


def _db(root: Path) -> None:
	(root / "20260719_120000-database.sql.gz").write_bytes(b"db")


def _tar(path: Path, member: str = "files/example.txt", content: bytes = b"expected") -> None:
	mode = "w:gz" if path.name.endswith((".tgz", ".tar.gz")) else "w"
	with tarfile.open(path, mode) as handle:
		info = tarfile.TarInfo(member)
		info.size = len(content)
		handle.addfile(info, io.BytesIO(content))


@pytest.mark.parametrize("suffix", [".tgz", ".tar.gz", ".tar"])
def test_native_public_and_private_formats(tmp_path: Path, suffix: str):
	_db(tmp_path)
	_tar(tmp_path / f"20260719_120000-files{suffix}")
	_tar(tmp_path / f"20260719_120000-private-files{suffix}")
	result = discover_backup_artifacts(tmp_path)
	assert result.public_files and result.public_files.endswith(f"-files{suffix}")
	assert result.private_files and result.private_files.endswith(f"-private-files{suffix}")


def test_database_only_backup(tmp_path: Path):
	_db(tmp_path)
	result = discover_backup_artifacts(tmp_path)
	assert result.database_only is True
	assert build_restore_args("site.test", result) == [
		"--site",
		"site.test",
		"restore",
		result.database,
		"--force",
	]


def test_public_only_restore_arguments(tmp_path: Path):
	_db(tmp_path)
	_tar(tmp_path / "x-files.tgz")
	result = discover_backup_artifacts(tmp_path)
	assert result.private_files is None
	assert build_restore_args("site.test", result)[-2:] == ["--with-public-files", result.public_files]


def test_private_only_restore_arguments(tmp_path: Path):
	_db(tmp_path)
	_tar(tmp_path / "x-private-files.tgz")
	result = discover_backup_artifacts(tmp_path)
	assert result.public_files is None
	assert build_restore_args("site.test", result)[-2:] == ["--with-private-files", result.private_files]


def test_private_archive_is_never_selected_as_public(tmp_path: Path):
	_db(tmp_path)
	private = tmp_path / "native-private-files.tar.gz"
	_tar(private)
	result = discover_backup_artifacts(tmp_path)
	assert result.private_files == str(private.resolve())
	assert result.public_files is None


def test_multiple_public_archives_fail_safely(tmp_path: Path):
	_db(tmp_path)
	_tar(tmp_path / "a-files.tgz")
	_tar(tmp_path / "b-files.tar.gz")
	with pytest.raises(ArtifactDiscoveryError, match="Ambiguous public-files"):
		discover_backup_artifacts(tmp_path)


def test_correct_restore_command_arguments_include_both_archives(tmp_path: Path):
	_db(tmp_path)
	_tar(tmp_path / "x-files.tgz")
	_tar(tmp_path / "x-private-files.tgz")
	result = discover_backup_artifacts(tmp_path)
	args = build_restore_args("site with spaces.test", result)
	assert args == [
		"--site",
		"site with spaces.test",
		"restore",
		result.database,
		"--force",
		"--with-private-files",
		result.private_files,
		"--with-public-files",
		result.public_files,
	]


def test_representative_restored_file_checksum(tmp_path: Path):
	archive = tmp_path / "x-files.tgz"
	_tar(archive, "files/example.txt", b"expected")
	site = tmp_path / "site"
	restored = site / "public" / "files" / "example.txt"
	restored.parent.mkdir(parents=True)
	restored.write_bytes(b"expected")
	result = verify_restored_archive(str(archive), str(site), "public")
	assert result["ok"] is True
	restored.write_bytes(b"tampered")
	result = verify_restored_archive(str(archive), str(site), "public")
	assert result["ok"] is False
