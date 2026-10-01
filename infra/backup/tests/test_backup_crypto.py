from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from infra.backup.backup_crypto import (
	BackupCryptoError,
	decrypt_backup,
	encrypt_backup,
	validate_encryption_config,
	verify_encrypted_artifact,
)

RECIPIENT = "age1" + "a" * 50


def _fake_age(tmp_path: Path) -> Path:
	script = tmp_path / "age"
	script.write_text(
		r"""#!/usr/bin/env python3
import pathlib, sys
args=sys.argv[1:]
if "--encrypt" in args:
    recipient=args[args.index("--recipient")+1]
    output=pathlib.Path(args[args.index("--output")+1])
    output.write_bytes(("FAKEAGE:"+recipient+"\n").encode()+sys.stdin.buffer.read())
    raise SystemExit(0)
if "--decrypt" in args:
    identity=pathlib.Path(args[args.index("--identity")+1]).read_text().strip()
    data=pathlib.Path(args[-1]).read_bytes()
    header, body=data.split(b"\n",1)
    recipient=header.decode().split(":",1)[1]
    if identity != recipient:
        raise SystemExit(3)
    sys.stdout.buffer.write(body)
    raise SystemExit(0)
raise SystemExit(2)
""",
		encoding="utf-8",
	)
	script.chmod(0o755)
	return script


def _env(tmp_path: Path, age: Path, *, production: bool = True) -> dict[str, str]:
	return {
		"AOS_ENVIRONMENT": "production" if production else "development",
		"BACKUP_ENCRYPTION_REQUIRED": "true" if production else "false",
		"BACKUP_ENCRYPTION_METHOD": "age",
		"BACKUP_AGE_RECIPIENT": RECIPIENT,
		"AGE_BINARY": str(age),
	}


def test_missing_production_encryption_configuration_fails_closed():
	report = validate_encryption_config({"AOS_ENVIRONMENT": "production", "BACKUP_ENCRYPTION_METHOD": "none"})
	assert report["ok"] is False
	assert "production_encryption_disabled" not in str(report)  # human-safe errors, no secrets


def test_placeholder_recipient_is_rejected(tmp_path: Path):
	age = _fake_age(tmp_path)
	report = validate_encryption_config(
		{
			"AOS_ENVIRONMENT": "production",
			"BACKUP_ENCRYPTION_METHOD": "age",
			"BACKUP_AGE_RECIPIENT": "age1-placeholder-change-me",
			"AGE_BINARY": str(age),
		}
	)
	assert report["ok"] is False


def test_encryption_decryption_integrity_and_cleanup(tmp_path: Path):
	age = _fake_age(tmp_path)
	backup = tmp_path / "20260719T120000Z"
	backup.mkdir()
	(backup / "secret.txt").write_text("sensitive backup content", encoding="utf-8")
	output = tmp_path / "encrypted" / "20260719T120000Z.tar.gz.age"
	env = _env(tmp_path, age)
	result = encrypt_backup(backup, output, env)
	assert result["method"] == "age"
	assert output.read_bytes().startswith(b"FAKEAGE:")
	assert b"sensitive backup content" not in output.read_bytes()
	assert verify_encrypted_artifact(output) == result["sha256"]
	assert not [path for path in output.parent.iterdir() if path.name.startswith(f".{output.name}.")]

	identity = tmp_path / "identity.txt"
	identity.write_text(RECIPIENT, encoding="utf-8")
	identity.chmod(0o600)
	restore_env = {**env, "BACKUP_AGE_IDENTITY_FILE": str(identity)}
	restored_root = decrypt_backup(output, tmp_path / "restore", restore_env)
	assert (restored_root / "secret.txt").read_text(encoding="utf-8") == "sensitive backup content"


def test_incorrect_identity_fails_without_leaking_key(tmp_path: Path):
	age = _fake_age(tmp_path)
	backup = tmp_path / "backup"
	backup.mkdir()
	(backup / "data").write_text("x", encoding="utf-8")
	output = tmp_path / "backup.tar.gz.age"
	env = _env(tmp_path, age)
	encrypt_backup(backup, output, env)
	identity = tmp_path / "identity"
	identity.write_text("age1" + "b" * 50, encoding="utf-8")
	identity.chmod(0o600)
	with pytest.raises(BackupCryptoError, match="identity may be unavailable or incorrect") as exc:
		decrypt_backup(output, tmp_path / "restore", {**env, "BACKUP_AGE_IDENTITY_FILE": str(identity)})
	assert RECIPIENT not in str(exc.value)


def _minimal_verified_backup(root: Path) -> Path:
	backup = root / "20260719T120000Z"
	frappe = backup / "frappe"
	frappe.mkdir(parents=True)
	(backup / "metadata.env").write_text("CREATED_AT_UTC=20260719T120000Z\n", encoding="utf-8")
	(frappe / "x-database.sql").write_text("db", encoding="utf-8")
	lines = []
	for path in [backup / "metadata.env", frappe / "x-database.sql"]:
		digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
		lines.append(f"{digest}  {path.relative_to(backup)}")
	(backup / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
	(backup / "VERIFIED_AT_UTC").write_text("2026-07-19T12:00:00Z\n", encoding="utf-8")
	return backup


def test_plaintext_offsite_copy_is_prevented_in_production(tmp_path: Path):
	backup = _minimal_verified_backup(tmp_path / "backups")
	copied = tmp_path / "copied"
	custom = tmp_path / "copy.sh"
	custom.write_text(f"#!/bin/sh\ntouch '{copied}'\n", encoding="utf-8")
	custom.chmod(0o755)
	env_file = tmp_path / "backup.env"
	env_file.write_text(
		"\n".join(
			[
				"AOS_ENVIRONMENT=production",
				f"BACKUP_ROOT={tmp_path / 'backups'}",
				"BACKUP_ENCRYPTION_REQUIRED=true",
				"BACKUP_ENCRYPTION_METHOD=none",
				"OFFSITE_BACKUP_MODE=custom",
				f"REMOTE_COPY_COMMAND={custom}",
			]
		)
		+ "\n",
		encoding="utf-8",
	)
	result = subprocess.run(
		["bash", "infra/backup/offsite-copy.sh", str(backup)],
		cwd=Path(__file__).resolve().parents[3],
		env={**os.environ, "AOS_BACKUP_ENV_FILE": str(env_file)},
		capture_output=True,
		text=True,
	)
	assert result.returncode != 0
	assert not copied.exists()
	assert "BACKUP_AGE" not in result.stdout + result.stderr


def test_encrypted_artifact_can_be_retried_offsite_after_plaintext_cleanup(tmp_path: Path):
	age = _fake_age(tmp_path)
	backup = tmp_path / "20260719T120000Z"
	backup.mkdir()
	(backup / "secret.txt").write_text("sensitive", encoding="utf-8")
	artifact = tmp_path / "encrypted" / "20260719T120000Z.tar.gz.age"
	encrypt_backup(backup, artifact, _env(tmp_path, age))
	shutil.rmtree(backup)

	captured = tmp_path / "captured.txt"
	custom = tmp_path / "copy.py"
	custom.write_text(
		"#!/usr/bin/env python3\n"
		"import pathlib, sys\n"
		f"pathlib.Path({str(captured)!r}).write_text(sys.argv[1], encoding='utf-8')\n",
		encoding="utf-8",
	)
	custom.chmod(0o755)
	marker = tmp_path / "offsite.env"
	env_file = tmp_path / "backup.env"
	env_file.write_text(
		"\n".join(
			[
				"AOS_ENVIRONMENT=production",
				f"BACKUP_ROOT={tmp_path}",
				"BACKUP_ENCRYPTION_REQUIRED=true",
				"BACKUP_ENCRYPTION_METHOD=age",
				f"BACKUP_AGE_RECIPIENT={RECIPIENT}",
				f"AGE_BINARY={age}",
				"OFFSITE_BACKUP_MODE=custom",
				f"REMOTE_COPY_COMMAND={custom}",
				f"OFFSITE_SYNC_MARKER={marker}",
			]
		)
		+ "\n",
		encoding="utf-8",
	)
	result = subprocess.run(
		["bash", "infra/backup/offsite-copy.sh", str(artifact)],
		cwd=Path(__file__).resolve().parents[3],
		env={**os.environ, "AOS_BACKUP_ENV_FILE": str(env_file)},
		capture_output=True,
		text=True,
	)
	assert result.returncode == 0, result.stderr
	assert captured.read_text(encoding="utf-8") == str(artifact)
	marker_text = marker.read_text(encoding="utf-8")
	assert "OFFSITE_BACKUP_ID=20260719T120000Z" in marker_text
	assert "OFFSITE_ENCRYPTION_METHOD=age" in marker_text
	assert "OFFSITE_ARTIFACT_KIND=encrypted-age" in marker_text
	assert "OFFSITE_ARTIFACT_NAME=20260719T120000Z.tar.gz.age" in marker_text
	assert f"OFFSITE_ENCRYPTED_SHA256={verify_encrypted_artifact(artifact)}" in marker_text
	assert not backup.exists()
