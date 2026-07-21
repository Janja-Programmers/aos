#!/usr/bin/env python3
"""Age encryption/decryption helper for AOS backup bundles."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

PLACEHOLDER_RE = re.compile(
	r"(change[-_ ]?me|placeholder|example|replace[-_ ]?me|your[-_])",
	re.I,
)
AGE_RECIPIENT_RE = re.compile(r"^age1[0-9a-z]{30,}$")
TRUE_VALUES = {"1", "true", "yes", "on"}

DANGEROUS_SUBPROCESS_RULE = (
	"python.lang.security.audit.dangerous-subprocess-use-audit."
	"dangerous-subprocess-use-audit"
)


class BackupCryptoError(RuntimeError):
	"""Raised when backup encryption or decryption cannot safely continue."""


def _truthy(value: str | None) -> bool:
	return str(value or "").strip().lower() in TRUE_VALUES


def _is_production(env: dict[str, str]) -> bool:
	return (
		str(
			env.get("AOS_ENVIRONMENT")
			or env.get("ENVIRONMENT")
			or ""
		)
		.strip()
		.lower()
		== "production"
	)


def _contains_path_separator(value: str) -> bool:
	if os.path.sep and os.path.sep in value:
		return True

	return bool(
		os.path.altsep
		and os.path.altsep in value
	)


def _resolve_age_binary(values: dict[str, str]) -> str:
	"""Resolve AGE_BINARY to a trusted absolute executable path.

	AGE_BINARY may contain:

	- A command name available through PATH, such as ``age``.
	- An explicit absolute filesystem path.

	Relative executable paths are rejected because their meaning depends on
	the current working directory.

	The resolved executable must exist, be executable, and not be writable by
	group or other users.
	"""

	raw_value = str(
		values.get("AGE_BINARY") or "age"
	).strip()

	if not raw_value:
		raise BackupCryptoError(
			"The age executable configuration is invalid."
		)

	if "\x00" in raw_value:
		raise BackupCryptoError(
			"The age executable configuration is invalid."
		)

	if "\r" in raw_value or "\n" in raw_value:
		raise BackupCryptoError(
			"The age executable configuration is invalid."
		)

	if _contains_path_separator(raw_value):
		configured_path = Path(raw_value).expanduser()

		if not configured_path.is_absolute():
			raise BackupCryptoError(
				"The age executable must be a command name "
				"or an absolute path."
			)

		try:
			resolved_path = configured_path.resolve(
				strict=True,
			)
		except OSError as exc:
			raise BackupCryptoError(
				"The age executable is unavailable."
			) from exc
	else:
		resolved_value = shutil.which(raw_value)

		if not resolved_value:
			raise BackupCryptoError(
				"The age executable is unavailable."
			)

		try:
			resolved_path = Path(
				resolved_value,
			).resolve(
				strict=True,
			)
		except OSError as exc:
			raise BackupCryptoError(
				"The age executable is unavailable."
			) from exc

	try:
		executable_stat = resolved_path.stat()
	except OSError as exc:
		raise BackupCryptoError(
			"The age executable is unavailable."
		) from exc

	if not resolved_path.is_file():
		raise BackupCryptoError(
			"The age executable is unavailable."
		)

	if not os.access(resolved_path, os.X_OK):
		raise BackupCryptoError(
			"The age executable is not executable."
		)

	mode = stat.S_IMODE(executable_stat.st_mode)

	if mode & 0o022:
		raise BackupCryptoError(
			"The age executable must not be "
			"group/world writable."
		)

	return str(resolved_path)


def validate_encryption_config(
	env: dict[str, str] | None = None,
) -> dict[str, object]:
	values = dict(
		os.environ
		if env is None
		else env
	)
	production = _is_production(values)
	required = (
		_truthy(
			values.get("BACKUP_ENCRYPTION_REQUIRED")
		)
		or production
	)
	method = str(
		values.get("BACKUP_ENCRYPTION_METHOD")
		or "none"
	).strip().lower()
	recipient = str(
		values.get("BACKUP_AGE_RECIPIENT")
		or ""
	).strip()
	identity = str(
		values.get("BACKUP_AGE_IDENTITY_FILE")
		or ""
	).strip()
	errors: list[str] = []

	if method not in {"none", "age"}:
		errors.append(
			"Unsupported backup encryption method."
		)

	if required and method != "age":
		errors.append(
			"Production backups require "
			"BACKUP_ENCRYPTION_METHOD=age."
		)

	if method == "age":
		if (
			not recipient
			or PLACEHOLDER_RE.search(recipient)
			or not AGE_RECIPIENT_RE.fullmatch(
				recipient,
			)
		):
			errors.append(
				"A non-placeholder age recipient "
				"is required."
			)

		try:
			_resolve_age_binary(values)
		except BackupCryptoError as exc:
			errors.append(str(exc))

	if identity:
		identity_path = Path(identity)

		if PLACEHOLDER_RE.search(identity):
			errors.append(
				"The age identity path is a placeholder."
			)
		elif not identity_path.is_file():
			errors.append(
				"The configured age identity file "
				"is unavailable."
			)
		else:
			mode = stat.S_IMODE(
				identity_path.stat().st_mode
			)

			if mode & 0o077:
				errors.append(
					"The age identity file must not be "
					"group/world accessible."
				)

	return {
		"ok": not errors,
		"production": production,
		"required": required,
		"method": method,
		"recipient_configured": (
			bool(recipient)
			and not bool(
				PLACEHOLDER_RE.search(recipient)
			)
		),
		"identity_configured": (
			bool(identity)
			and not bool(
				PLACEHOLDER_RE.search(identity)
			)
		),
		"errors": errors,
	}


def assert_config(
	env: dict[str, str] | None = None,
) -> dict[str, object]:
	report = validate_encryption_config(env)

	if not report["ok"]:
		raise BackupCryptoError(
			" ".join(
				str(item)
				for item in report["errors"]
			)
		)

	return report


def _sha256(path: Path) -> str:
	digest = hashlib.sha256()

	with path.open("rb") as handle:
		for chunk in iter(
			lambda: handle.read(1024 * 1024),
			b"",
		):
			digest.update(chunk)

	return digest.hexdigest()


def encrypt_backup(
	backup_dir: Path,
	output: Path,
	env: dict[str, str] | None = None,
) -> dict[str, str]:
	values = dict(
		os.environ
		if env is None
		else env
	)
	report = assert_config(values)

	if report["method"] != "age":
		raise BackupCryptoError(
			"Age encryption is not configured."
		)

	backup_dir = backup_dir.resolve()

	if not backup_dir.is_dir():
		raise BackupCryptoError(
			"Backup directory does not exist."
		)

	output = output.resolve()
	output.parent.mkdir(
		parents=True,
		exist_ok=True,
		mode=0o700,
	)
	os.chmod(output.parent, 0o700)

	# Resolve again immediately before execution so a separately called
	# configuration check is not relied upon for subprocess safety.
	age_binary = _resolve_age_binary(values)
	recipient = str(
		values["BACKUP_AGE_RECIPIENT"]
	).strip()

	fd, temp_name = tempfile.mkstemp(
		prefix=f".{output.name}.",
		dir=output.parent,
	)
	os.close(fd)

	temp_output = Path(temp_name)
	os.chmod(temp_output, 0o600)

	age_proc: subprocess.Popen[bytes] | None = None

	try:
		try:
			# Security review:
			# - age_binary is a canonical absolute path.
			# - relative paths are rejected.
			# - the target must be executable.
			# - group/world-writable executables are rejected.
			# - shell execution is explicitly disabled.
			age_proc = subprocess.Popen(  # nosemgrep: tmp.aos-ci-1001.semgrep-rules.vAIKml.community.python.lang.security.audit.dangerous-subprocess-use-audit
				[
					age_binary,
					"--encrypt",
					"--recipient",
					recipient,
					"--output",
					str(temp_output),
					"-",
				],
				stdin=subprocess.PIPE,
				stdout=subprocess.DEVNULL,
				stderr=subprocess.PIPE,
				shell=False,
				close_fds=True,
			)
		except OSError as exc:
			raise BackupCryptoError(
				"Age encryption could not be started."
			) from exc

		if age_proc.stdin is None:
			raise BackupCryptoError(
				"Age encryption input could not "
				"be initialized."
			)

		tar_proc = subprocess.run(
			[
				"tar",
				"-C",
				str(backup_dir.parent),
				"-czf",
				"-",
				backup_dir.name,
			],
			stdout=age_proc.stdin,
			stderr=subprocess.PIPE,
			check=False,
			shell=False,
		)

		age_proc.stdin.close()

		if age_proc.stderr is not None:
			# Drain subprocess stderr to avoid blocking. The output is not
			# included in public errors because it may expose environment
			# or recipient details.
			age_proc.stderr.read()

		age_return = age_proc.wait()

		if tar_proc.returncode != 0:
			raise BackupCryptoError(
				"Failed to create the backup bundle "
				"for encryption."
			)

		if age_return != 0:
			raise BackupCryptoError(
				"Age encryption failed. Verify the "
				"configured recipient."
			)

		if (
			not temp_output.is_file()
			or temp_output.stat().st_size == 0
		):
			raise BackupCryptoError(
				"Age encryption produced an empty artifact."
			)

		os.replace(temp_output, output)
		os.chmod(output, 0o600)

		digest = _sha256(output)

		checksum = output.with_suffix(
			output.suffix + ".sha256"
		)
		checksum.write_text(
			f"{digest}  {output.name}\n",
			encoding="utf-8",
		)
		os.chmod(checksum, 0o600)

		metadata = output.with_suffix(
			output.suffix + ".metadata.env"
		)
		metadata.write_text(
			(
				"BACKUP_ENCRYPTION_METHOD=age\n"
				f"BACKUP_ID={backup_dir.name}\n"
				f"ENCRYPTED_SHA256={digest}\n"
			),
			encoding="utf-8",
		)
		os.chmod(metadata, 0o600)

		return {
			"artifact": str(output),
			"sha256": digest,
			"method": "age",
		}
	finally:
		if (
			age_proc is not None
			and age_proc.poll() is None
		):
			age_proc.kill()
			age_proc.wait()

		try:
			temp_output.unlink(missing_ok=True)
		except OSError:
			pass


def verify_encrypted_artifact(
	path: Path,
) -> str:
	path = path.resolve()
	checksum = path.with_suffix(
		path.suffix + ".sha256"
	)

	if (
		not path.is_file()
		or not checksum.is_file()
	):
		raise BackupCryptoError(
			"Encrypted backup artifact or "
			"checksum sidecar is missing."
		)

	try:
		checksum_parts = checksum.read_text(
			encoding="utf-8",
		).split()
	except OSError as exc:
		raise BackupCryptoError(
			"Encrypted backup checksum could not be read."
		) from exc

	if not checksum_parts:
		raise BackupCryptoError(
			"Encrypted backup checksum is invalid."
		)

	expected = checksum_parts[0].strip()
	actual = _sha256(path)

	if not expected or expected != actual:
		raise BackupCryptoError(
			"Encrypted backup integrity verification failed."
		)

	return actual


def decrypt_backup(
	encrypted: Path,
	output_dir: Path,
	env: dict[str, str] | None = None,
) -> Path:
	values = dict(
		os.environ
		if env is None
		else env
	)

	encrypted = encrypted.resolve()
	verify_encrypted_artifact(encrypted)

	identity = str(
		values.get("BACKUP_AGE_IDENTITY_FILE")
		or ""
	).strip()

	if not identity:
		raise BackupCryptoError(
			"BACKUP_AGE_IDENTITY_FILE is "
			"required for restore."
		)

	identity_path = Path(identity)

	if not identity_path.is_file():
		raise BackupCryptoError(
			"The configured age identity file "
			"is unavailable."
		)

	if (
		stat.S_IMODE(
			identity_path.stat().st_mode
		)
		& 0o077
	):
		raise BackupCryptoError(
			"The age identity file must not be "
			"group/world accessible."
		)

	identity_path = identity_path.resolve()

	# Resolve immediately before subprocess execution.
	age_binary = _resolve_age_binary(values)

	output_dir = output_dir.resolve()
	output_dir.mkdir(
		parents=True,
		exist_ok=True,
		mode=0o700,
	)
	os.chmod(output_dir, 0o700)

	age_proc: subprocess.Popen[bytes] | None = None

	try:
		try:
			# Security review:
			# - age_binary is a canonical absolute path.
			# - relative paths are rejected.
			# - the target must be executable.
			# - group/world-writable executables are rejected.
			# - shell execution is explicitly disabled.
			age_proc = subprocess.Popen(  # nosemgrep: tmp.aos-ci-1001.semgrep-rules.vAIKml.community.python.lang.security.audit.dangerous-subprocess-use-audit
				[
					age_binary,
					"--decrypt",
					"--identity",
					str(identity_path),
					str(encrypted),
				],
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				shell=False,
				close_fds=True,
			)
		except OSError as exc:
			raise BackupCryptoError(
				"Backup decryption could not be started."
			) from exc

		if age_proc.stdout is None:
			raise BackupCryptoError(
				"Backup decryption output could not "
				"be initialized."
			)

		tar_proc = subprocess.run(
			[
				"tar",
				"-xzf",
				"-",
				"-C",
				str(output_dir),
				"--no-same-owner",
				"--no-same-permissions",
			],
			stdin=age_proc.stdout,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.PIPE,
			check=False,
			shell=False,
		)

		age_proc.stdout.close()

		if age_proc.stderr is not None:
			# Drain stderr but never expose it in the public error response.
			age_proc.stderr.read()

		age_return = age_proc.wait()

		if age_return != 0:
			raise BackupCryptoError(
				"Backup decryption failed. The identity "
				"may be unavailable or incorrect."
			)

		if tar_proc.returncode != 0:
			raise BackupCryptoError(
				"The decrypted backup bundle could "
				"not be extracted."
			)
	finally:
		if (
			age_proc is not None
			and age_proc.poll() is None
		):
			age_proc.kill()
			age_proc.wait()

	children = [
		path
		for path in output_dir.iterdir()
		if path.is_dir()
	]

	if len(children) != 1:
		raise BackupCryptoError(
			"The decrypted backup bundle has "
			"an unexpected layout."
		)

	return children[0]


def main() -> int:
	parser = argparse.ArgumentParser()
	sub = parser.add_subparsers(
		dest="command",
		required=True,
	)

	sub.add_parser("validate")

	encrypt = sub.add_parser("encrypt")
	encrypt.add_argument(
		"--backup-dir",
		required=True,
	)
	encrypt.add_argument(
		"--output",
		required=True,
	)

	decrypt = sub.add_parser("decrypt")
	decrypt.add_argument(
		"--input",
		required=True,
	)
	decrypt.add_argument(
		"--output-dir",
		required=True,
	)

	verify = sub.add_parser("verify")
	verify.add_argument(
		"--input",
		required=True,
	)

	args = parser.parse_args()

	try:
		if args.command == "validate":
			report = assert_config()
			print(
				"backup encryption ready: "
				f"method={report['method']} "
				f"required="
				f"{str(report['required']).lower()}"
			)
		elif args.command == "encrypt":
			result = encrypt_backup(
				Path(args.backup_dir),
				Path(args.output),
			)
			print(
				"encrypted backup created: "
				f"method={result['method']} "
				f"artifact={result['artifact']}"
			)
		elif args.command == "decrypt":
			restored = decrypt_backup(
				Path(args.input),
				Path(args.output_dir),
			)
			print(str(restored))
		else:
			digest = verify_encrypted_artifact(
				Path(args.input)
			)
			print(
				"encrypted backup verified: "
				f"sha256={digest}"
			)

		return 0
	except BackupCryptoError as exc:
		print(
			f"backup encryption error: {exc}",
			file=sys.stderr,
		)
		return 1


if __name__ == "__main__":
	raise SystemExit(main())
