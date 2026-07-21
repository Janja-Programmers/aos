#!/usr/bin/env python3
"""Discover and verify AOS/Frappe backup artifacts safely.

The CLI intentionally emits no secret configuration. Restore scripts consume the
NUL-delimited output so paths containing whitespace remain safe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tarfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

ARCHIVE_SUFFIXES = (".tgz", ".tar.gz", ".tar")
_DB_RE = re.compile(r"(?:^|[-_])database\.sql(?:\.gz)?$|\.sql(?:\.gz)?$", re.IGNORECASE)
_PRIVATE_RE = re.compile(r"-private-files(?:\.tgz|\.tar\.gz|\.tar)$", re.IGNORECASE)
_PUBLIC_RE = re.compile(r"-files(?:\.tgz|\.tar\.gz|\.tar)$", re.IGNORECASE)


class ArtifactDiscoveryError(RuntimeError):
	pass


@dataclass(frozen=True)
class BackupArtifacts:
	database: str
	public_files: str | None
	private_files: str | None

	@property
	def database_only(self) -> bool:
		return not self.public_files and not self.private_files


def _matches_database(name: str) -> bool:
	return bool(_DB_RE.search(name))


def _matches_private(name: str) -> bool:
	return bool(_PRIVATE_RE.search(name))


def _matches_public(name: str) -> bool:
	# Private archives also end with "-files"; exclude them before matching.
	return not _matches_private(name) and bool(_PUBLIC_RE.search(name))


def _resolve_one(kind: str, candidates: Iterable[Path], *, allow_missing: bool) -> str | None:
	paths = sorted((path.resolve() for path in candidates), key=lambda item: item.name)
	if not paths:
		if allow_missing:
			return None
		raise ArtifactDiscoveryError(f"No {kind} backup artifact was found.")
	if len(paths) > 1:
		names = ", ".join(path.name for path in paths)
		raise ArtifactDiscoveryError(f"Ambiguous {kind} backup artifacts: {names}")
	return str(paths[0])


def discover_backup_artifacts(frappe_dir: str | os.PathLike[str]) -> BackupArtifacts:
	root = Path(frappe_dir)
	if not root.is_dir():
		raise ArtifactDiscoveryError(f"Frappe backup directory does not exist: {root}")

	files = [path for path in root.iterdir() if path.is_file()]
	database = _resolve_one(
		"database", (path for path in files if _matches_database(path.name)), allow_missing=False
	)
	private_files = _resolve_one(
		"private-files", (path for path in files if _matches_private(path.name)), allow_missing=True
	)
	public_files = _resolve_one(
		"public-files", (path for path in files if _matches_public(path.name)), allow_missing=True
	)
	assert database is not None
	return BackupArtifacts(database=database, public_files=public_files, private_files=private_files)


def build_restore_args(site: str, artifacts: BackupArtifacts) -> list[str]:
	site = str(site or "").strip()
	if not site:
		raise ArtifactDiscoveryError("Frappe site is required.")
	args = ["--site", site, "restore", artifacts.database, "--force"]
	if artifacts.private_files:
		args.extend(["--with-private-files", artifacts.private_files])
	if artifacts.public_files:
		args.extend(["--with-public-files", artifacts.public_files])
	return args


def _safe_member_name(name: str) -> str | None:
	normalized = str(PurePosixPath(name.lstrip("./")))
	if not normalized or normalized == ".":
		return None
	pure = PurePosixPath(normalized)
	if pure.is_absolute() or ".." in pure.parts:
		return None
	return normalized


def _candidate_restored_paths(site_root: Path, member_name: str, kind: str) -> list[Path]:
	normalized = member_name.lstrip("./")
	prefixes = ["sites/", "public/", "private/"]
	variants = {normalized}
	for prefix in prefixes:
		if normalized.startswith(prefix):
			variants.add(normalized[len(prefix) :])
	if normalized.startswith("files/"):
		variants.add(normalized)
	else:
		variants.add(f"files/{normalized}")

	base = site_root / ("private" if kind == "private" else "public")
	candidates: list[Path] = []
	for variant in sorted(variants):
		if variant.startswith("private/files/"):
			candidates.append(site_root / variant)
		elif variant.startswith("public/files/"):
			candidates.append(site_root / variant)
		else:
			candidates.append(base / variant)
	return candidates


def verify_restored_archive(archive: str, site_root: str, kind: str) -> dict[str, object]:
	if kind not in {"public", "private"}:
		raise ArtifactDiscoveryError("kind must be public or private")
	archive_path = Path(archive)
	site_path = Path(site_root)
	if not archive_path.is_file():
		raise ArtifactDiscoveryError(f"Archive does not exist: {archive_path}")
	if not site_path.is_dir():
		raise ArtifactDiscoveryError(f"Restored site root does not exist: {site_path}")

	checked = 0
	with tarfile.open(archive_path, mode="r:*") as handle:
		members = sorted(
			(member for member in handle.getmembers() if member.isfile()), key=lambda item: item.name
		)
		for member in members:
			safe_name = _safe_member_name(member.name)
			if not safe_name:
				continue
			extracted = handle.extractfile(member)
			if extracted is None:
				continue
			expected = hashlib.sha256(extracted.read()).hexdigest()
			checked += 1
			for candidate in _candidate_restored_paths(site_path, safe_name, kind):
				try:
					resolved = candidate.resolve(strict=True)
					resolved.relative_to(site_path.resolve())
				except (FileNotFoundError, ValueError, OSError):
					continue
				if not resolved.is_file():
					continue
				actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
				return {
					"ok": actual == expected,
					"kind": kind,
					"member": safe_name,
					"expected_sha256": expected,
					"actual_sha256": actual,
					"readable": os.access(resolved, os.R_OK),
					"checked_members": checked,
				}
	return {
		"ok": False,
		"kind": kind,
		"member": None,
		"expected_sha256": None,
		"actual_sha256": None,
		"readable": False,
		"checked_members": checked,
		"error": "No representative archive member was found at the restored site path.",
	}


def _emit_null(artifacts: BackupArtifacts) -> None:
	values = (
		"database",
		artifacts.database,
		"public_files",
		artifacts.public_files or "",
		"private_files",
		artifacts.private_files or "",
	)
	sys.stdout.buffer.write(b"\0".join(value.encode("utf-8") for value in values) + b"\0")


def main() -> int:
	parser = argparse.ArgumentParser()
	sub = parser.add_subparsers(dest="command", required=True)

	discover = sub.add_parser("discover")
	discover.add_argument("frappe_dir")
	discover.add_argument("--format", choices=("json", "null"), default="json")

	restore_args = sub.add_parser("restore-args")
	restore_args.add_argument("frappe_dir")
	restore_args.add_argument("--site", required=True)
	restore_args.add_argument("--format", choices=("json", "null"), default="json")

	verify = sub.add_parser("verify-restored-archive")
	verify.add_argument("--archive", required=True)
	verify.add_argument("--site-root", required=True)
	verify.add_argument("--kind", choices=("public", "private"), required=True)

	args = parser.parse_args()
	try:
		artifacts = discover_backup_artifacts(args.frappe_dir) if hasattr(args, "frappe_dir") else None
		if args.command == "discover":
			assert artifacts is not None
			if args.format == "null":
				_emit_null(artifacts)
			else:
				print(
					json.dumps(
						{**asdict(artifacts), "database_only": artifacts.database_only}, sort_keys=True
					)
				)
		elif args.command == "restore-args":
			assert artifacts is not None
			values = build_restore_args(args.site, artifacts)
			if args.format == "null":
				sys.stdout.buffer.write(b"\0".join(value.encode("utf-8") for value in values) + b"\0")
			else:
				print(json.dumps(values))
		else:
			print(
				json.dumps(verify_restored_archive(args.archive, args.site_root, args.kind), sort_keys=True)
			)
		return 0
	except ArtifactDiscoveryError as exc:
		print(f"backup artifact error: {exc}", file=sys.stderr)
		return 2


if __name__ == "__main__":
	raise SystemExit(main())
