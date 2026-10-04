from __future__ import annotations

import os
import subprocess
from pathlib import Path

RECIPIENT = "age1" + "a" * 50
ROOT = Path(__file__).resolve().parents[3]


def _write_executable(path: Path, body: str) -> None:
	path.write_text(body, encoding="utf-8")
	path.chmod(0o755)


def _layout(tmp_path: Path, *, fail_age: bool = False) -> tuple[Path, Path]:
	bin_dir = tmp_path / "bin"
	bin_dir.mkdir()
	bench_root = tmp_path / "bench"
	site = "site.test"
	backup_source = bench_root / "sites" / site / "private" / "backups"
	backup_source.mkdir(parents=True)
	# Pre-existing native backups must remain untouched and must never be swept
	# into the new encrypted archive based on file modification timestamps.
	(backup_source / "previous-database.sql.gz").write_bytes(b"PREVIOUS-BACKUP-NOT-CURRENT")
	(bench_root / "sites" / site / "site_config.json").write_text("{}", encoding="utf-8")
	backup_root = tmp_path / "backups"
	backup_root.mkdir()

	_write_executable(
		bin_dir / "bench",
		r"""#!/usr/bin/env python3
import io, os, pathlib, sys, tarfile
args=sys.argv[1:]
assert "--backup-path" in args, "Native Frappe output must be explicitly scoped"
root=pathlib.Path(args[args.index("--backup-path")+1])
root.mkdir(parents=True, exist_ok=True)
(root/"x-database.sql.gz").write_bytes(b"database-sensitive-content")
for name, member, content in [
    ("x-files.tgz", "files/public.txt", b"public-sensitive-content"),
    ("x-private-files.tgz", "private/files/private.txt", b"private-sensitive-content"),
]:
    with tarfile.open(root/name, "w:gz") as handle:
        info=tarfile.TarInfo(member); info.size=len(content); handle.addfile(info, io.BytesIO(content))
""",
	)
	_write_executable(bin_dir / "docker", "#!/bin/sh\nexit 0\n")
	_write_executable(bin_dir / "rsync", "#!/bin/sh\nexit 0\n")
	_write_executable(
		bin_dir / "age",
		r"""#!/usr/bin/env python3
import os, pathlib, sys
args=sys.argv[1:]
if os.getenv("FAIL_AGE") == "1": raise SystemExit(7)
if "--encrypt" in args:
    out=pathlib.Path(args[args.index("--output")+1]); out.write_bytes(b"FAKEAGE\n"+sys.stdin.buffer.read()); raise SystemExit(0)
raise SystemExit(2)
""",
	)
	env_file = tmp_path / "backup.env"
	env_file.write_text(
		"\n".join(
			[
				f"AOS_REPO_ROOT={ROOT}",
				f"FRAPPE_BENCH_ROOT={bench_root}",
				f"FRAPPE_SITE={site}",
				f"BACKUP_ROOT={backup_root}",
				"AOS_ENVIRONMENT=production",
				"BACKUP_ENCRYPTION_REQUIRED=true",
				"BACKUP_ENCRYPTION_METHOD=age",
				f"BACKUP_AGE_RECIPIENT={RECIPIENT}",
				f"AGE_BINARY={bin_dir / 'age'}",
				"BACKUP_LOCAL_RETENTION_MODE=encrypted-artifact",
				f"PLAINTEXT_WORK_ROOT={backup_root / '.plaintext-work'}",
				f"ENCRYPTED_BACKUP_ROOT={backup_root / 'encrypted'}",
				"QUIESCE_DOCKER_VOLUMES=false",
				"INCLUDE_MINIO_DATA=false",
				"INCLUDE_QDRANT_DATA=false",
				"INCLUDE_NOMINATIM_DATA=false",
				"INCLUDE_MAP_ARTIFACTS=false",
				"INCLUDE_CONFIGURATION=false",
				"OFFSITE_BACKUP_ENABLED=false",
			]
		)
		+ "\n",
		encoding="utf-8",
	)
	env = {
		**os.environ,
		"PATH": f"{bin_dir}:{os.environ['PATH']}",
		"AOS_BACKUP_ENV_FILE": str(env_file),
		"FAIL_AGE": "1" if fail_age else "0",
	}
	return backup_root, env


def _run(tmp_path: Path, *, fail_age: bool = False) -> tuple[subprocess.CompletedProcess[str], Path]:
	backup_root, env = _layout(tmp_path, fail_age=fail_age)
	result = subprocess.run(
		["bash", str(ROOT / "infra/backup/backup.sh")],
		cwd=ROOT,
		env=env,
		capture_output=True,
		text=True,
		timeout=60,
	)
	return result, backup_root


def test_production_retains_only_verified_encrypted_artifact(tmp_path: Path):
	result, backup_root = _run(tmp_path)
	assert result.returncode == 0, result.stdout + result.stderr
	original = (
		tmp_path / "bench" / "sites" / "site.test" / "private" / "backups"
	)
	assert sorted(path.name for path in original.iterdir()) == ["previous-database.sql.gz"]
	assert (original / "previous-database.sql.gz").read_bytes() == b"PREVIOUS-BACKUP-NOT-CURRENT"
	artifacts = list((backup_root / "encrypted").glob("*.tar.gz.age"))
	assert len(artifacts) == 1
	assert Path(str(artifacts[0]) + ".sha256").is_file()
	# The fake age CLI prepends a fixed test header to the archive stream.
	# Assert that only this run's native archives entered the encrypted bundle.
	import io
	import tarfile
	with tarfile.open(fileobj=io.BytesIO(artifacts[0].read_bytes().split(b"\\n", 1)[1]), mode="r:gz") as archive:
		names = archive.getnames()
	assert not any("previous-database.sql.gz" in name for name in names)
	assert any("x-database.sql.gz" in name for name in names)
	assert any("x-private-files.tgz" in name for name in names)
	assert any("x-files.tgz" in name for name in names)
	metadata = Path(str(artifacts[0]) + ".metadata.env").read_text(encoding="utf-8")
	assert "PLAINTEXT_LOCAL_RETAINED=false" in metadata
	assert "ENCRYPTED_ARTIFACT_VERIFIED=true" in metadata
	assert not list(backup_root.glob("20????????T??????Z"))
	work_root = backup_root / ".plaintext-work"
	assert not work_root.exists() or not list(work_root.iterdir())


def test_plaintext_workspace_is_cleaned_when_encryption_fails(tmp_path: Path):
	result, backup_root = _run(tmp_path, fail_age=True)
	assert result.returncode != 0
	assert not list(backup_root.glob("20????????T??????Z"))
	work_root = backup_root / ".plaintext-work"
	assert not work_root.exists() or not list(work_root.iterdir())
	assert not list((backup_root / "encrypted").glob("*.tar.gz.age"))
	original = tmp_path / "bench" / "sites" / "site.test" / "private" / "backups"
	assert sorted(path.name for path in original.iterdir()) == ["previous-database.sql.gz"]
