from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_RE = re.compile(r"^\s*image:\s*([^#\s]+@(?P<digest>sha256:[0-9a-f]{64}))\s*(?:#.*)?$")


def sha256(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open("rb") as handle:
		for chunk in iter(lambda: handle.read(1024 * 1024), b""):
			digest.update(chunk)
	return digest.hexdigest()


def _repository_image_digests() -> dict[str, str]:
	root = Path(__file__).resolve().parents[2]
	found: dict[str, str] = {}
	for path in sorted(root.glob("*compose*.y*ml")):
		for line in path.read_text(encoding="utf-8").splitlines():
			match = _IMAGE_RE.match(line)
			if not match:
				continue
			reference = match.group(1)
			image = reference.split("@", 1)[0]
			found[image] = match.group("digest")
	return found


def _configured_image_digests() -> dict[str, str]:
	raw = os.getenv("AOS_IMAGE_DIGESTS_JSON", "").strip()
	if not raw:
		return _repository_image_digests()
	data = json.loads(raw)
	if not isinstance(data, dict):
		raise SystemExit("AOS_IMAGE_DIGESTS_JSON must be an object of image names to sha256 digests.")
	return {str(key): str(value) for key, value in data.items()}


def _validate_manifest_data(data: dict[str, Any], commit: str) -> None:
	if not _COMMIT_RE.fullmatch(commit):
		raise SystemExit("Release commit must be a full immutable Git SHA.")
	if data.get("schema_version") != 1:
		raise SystemExit("Unsupported release manifest schema.")
	if data.get("commit") != commit:
		raise SystemExit("Release manifest commit mismatch.")
	artifact_digest = str(data.get("artifact_sha256") or "")
	if not re.fullmatch(r"[0-9a-f]{64}", artifact_digest):
		raise SystemExit("Release artifact digest is invalid.")
	digests = data.get("container_image_digests")
	if not isinstance(digests, dict) or not digests:
		raise SystemExit("Release manifest must record immutable container image digests.")
	for name, digest in digests.items():
		if not str(name).strip() or not _DIGEST_RE.fullmatch(str(digest)):
			raise SystemExit(f"Invalid immutable image digest declaration for {name}.")


def create(out: Path, artifact: Path, commit: str) -> None:
	payload = {
		"schema_version": 1,
		"commit": commit,
		"artifact": artifact.name,
		"artifact_sha256": sha256(artifact),
		"container_image_digests": _configured_image_digests(),
		"created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
		"workflow_run_id": os.getenv("GITHUB_RUN_ID", ""),
	}
	_validate_manifest_data(payload, commit)
	out.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def verify_manifest(manifest: Path, commit: str) -> dict[str, Any]:
	data = json.loads(manifest.read_text(encoding="utf-8"))
	if not isinstance(data, dict):
		raise SystemExit("Release manifest must be a JSON object.")
	_validate_manifest_data(data, commit)
	return data


def verify(manifest: Path, artifact: Path, commit: str) -> None:
	data = verify_manifest(manifest, commit)
	if data.get("artifact") != artifact.name:
		raise SystemExit("Release artifact filename mismatch.")
	if data.get("artifact_sha256") != sha256(artifact):
		raise SystemExit("Release artifact checksum mismatch.")


def main() -> None:
	if len(sys.argv) < 2:
		raise SystemExit("usage: release_manifest.py create|verify|verify-manifest ...")
	command = sys.argv[1]
	if command == "create" and len(sys.argv) == 5:
		create(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
	elif command == "verify" and len(sys.argv) == 5:
		verify(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
	elif command == "verify-manifest" and len(sys.argv) == 4:
		verify_manifest(Path(sys.argv[2]), sys.argv[3])
	else:
		raise SystemExit("invalid release-manifest command arguments")


if __name__ == "__main__":
	main()
