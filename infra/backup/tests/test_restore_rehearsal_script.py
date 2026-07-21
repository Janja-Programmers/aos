from __future__ import annotations

import hashlib
import io
import os
import subprocess
import tarfile
from pathlib import Path


def _tar(path: Path, member: str, content: bytes) -> None:
    with tarfile.open(path, "w:gz") as handle:
        info = tarfile.TarInfo(member)
        info.size = len(content)
        handle.addfile(info, io.BytesIO(content))


def _write_checksums(backup: Path) -> None:
    rows: list[str] = []
    for path in sorted(item for item in backup.rglob("*") if item.is_file() and item.name != "SHA256SUMS"):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {path.relative_to(backup).as_posix()}\n")
    (backup / "SHA256SUMS").write_text("".join(rows), encoding="utf-8")


def test_rehearsal_failure_never_writes_passing_marker(tmp_path: Path) -> None:
    backup = tmp_path / "backups" / "20260720T120000Z"
    frappe_dir = backup / "frappe"
    frappe_dir.mkdir(parents=True)
    (frappe_dir / "x-database.sql.gz").write_bytes(b"database")
    _tar(frappe_dir / "x-files.tgz", "files/public.txt", b"public")
    _tar(frappe_dir / "x-private-files.tgz", "private/files/private.txt", b"private")
    (backup / "metadata.env").write_text(
        "CREATED_AT_UTC=2026-07-20T11:00:00Z\nAOS_GIT_COMMIT=test-commit\n",
        encoding="utf-8",
    )
    _write_checksums(backup)

    restore_state_root = tmp_path / "restore-state"
    restore_state_root.mkdir()
    restore_state = restore_state_root / "rehearsal.local.env"
    restore_state.write_text(
        "\n".join(
            [
                f"BACKUP_ID={backup.name}",
                "DATABASE_RESTORE_RESULT=restored",
                "DB_RESTORED_AT_UTC=2026-07-20T12:00:00Z",
                "RESTORE_COMPLETED_AT_UTC=2026-07-20T12:10:00Z",
                "MIGRATION_RESULT=passed",
                "CHECKSUM_VERIFICATION_RESULT=failed",
                "PUBLIC_FILES_RESTORE_RESULT=failed",
                "PUBLIC_REPRESENTATIVE_VERIFY_RESULT=failed",
                "PRIVATE_FILES_RESTORE_RESULT=checksum_matched",
                "PRIVATE_REPRESENTATIVE_VERIFY_RESULT=passed",
                "",
            ]
        ),
        encoding="utf-8",
    )

    marker = tmp_path / "restore-rehearsal-passed.env"
    bench_root = tmp_path / "bench"
    bench_root.mkdir()
    env_file = tmp_path / "backup.env"
    env_file.write_text(
        "\n".join(
            [
                f"FRAPPE_BENCH_ROOT={bench_root}",
                "FRAPPE_SITE=rehearsal.local",
                f"BACKUP_ROOT={tmp_path / 'backups'}",
                f"RESTORE_STATE_ROOT={restore_state_root}",
                f"RESTORE_REHEARSAL_MARKER={marker}",
                "RESTORE_REHEARSAL_ENVIRONMENT=rehearsal",
                "RESTORE_REHEARSAL_REQUIRE_FILES=true",
                "RESTORE_REHEARSAL_ALLOW_DATABASE_ONLY=false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    script = Path(__file__).resolve().parents[1] / "restore-rehearsal-checklist.sh"
    environment = os.environ.copy()
    environment["AOS_BACKUP_ENV_FILE"] = str(env_file)
    result = subprocess.run(
        [str(script), "--backup", str(backup), "--mark-passed"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )

    assert result.returncode != 0
    assert not marker.exists()
    combined = f"{result.stdout}\n{result.stderr}"
    assert "public_restored_file_verification_failed" in combined or "checksum" in combined.lower()
