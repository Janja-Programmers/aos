from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EXTERNAL_IMAGE_RE = re.compile(r"^([^\\s@]+)@(sha256:[0-9a-f]{64})$")
_IMAGE_VARIABLE_RE = re.compile(r"^\\$\\{([A-Z][A-Z0-9_]*):\\?[^}]+\\}$")
_SERVICE_RE = re.compile(r"^  ([a-z][a-z0-9_-]*):\\s*(?:#.*)?$")
_IMAGE_LINE_RE = re.compile(r"^    image:\\s+(.+?)\\s*$")
_CONTEXT_LINE_RE = re.compile(r"^      context:\\s+(\\S+)\\s*$")
_DOCKERFILE_LINE_RE = re.compile(r"^      dockerfile:\\s+(\\S+)\\s*$")


def sha256(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open("rb") as handle:
		for chunk in iter(lambda: handle.read(1024 * 1024), b""):
			digest.update(chunk)
	return digest.hexdigest()


def _safe_archive_path(raw: str) -> str:
	path = raw.removeprefix("./").rstrip("/")
	if not path or path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
		raise ValueError(f"Invalid release source path: {raw!r}")
	return path


def _compose_services(text: str) -> dict[str, dict[str, str]]:
	lines = text.splitlines()
	if lines.count("services:") != 1:
		raise ValueError("Release archive must have one canonical Compose services section.")
	services: dict[str, dict[str, str]] = {}
	current: dict[str, str] | None = None
	for line in lines[lines.index("services:") + 1 :]:
		if line and not line.startswith((" ", "#")):
			break
		service = _SERVICE_RE.fullmatch(line)
		if service:
			name = service.group(1)
			if name in services:
				raise ValueError(f"Duplicate Compose service: {name}")
			current = {}
			services[name] = current
			continue
		if current is None:
			continue
		if line == "    build:":
			current["build"] = "true"
			continue
		for key, pattern in (
			("image", _IMAGE_LINE_RE),
			("context", _CONTEXT_LINE_RE),
			("dockerfile", _DOCKERFILE_LINE_RE),
		):
			match = pattern.fullmatch(line)
			if match:
				if key in current:
					raise ValueError(f"Duplicate {key} in Compose service.")
				current[key] = match.group(1)
				break
	if not services:
		raise ValueError("No Compose services were found in the release source.")
	return services


def _archive_inventory(artifact: Path) -> tuple[dict[str, str], dict[str, dict[str, str]], dict[str, str]]:
	with tarfile.open(artifact, mode="r:gz") as archive:
		members: dict[str, tarfile.TarInfo] = {}
		for member in archive:
			path = _safe_archive_path(member.name)
			if path in members:
				raise ValueError(f"Duplicate release archive path: {path}")
			members[path] = member
		compose = members.get("docker-compose.yml")
		if compose is None or not compose.isfile():
			raise ValueError("Release archive is missing docker-compose.yml.")
		compose_file = archive.extractfile(compose)
		if compose_file is None:
			raise ValueError("Cannot read archived Compose source.")
		services = _compose_services(compose_file.read().decode("utf-8"))
		images: dict[str, str] = {}
		builds: dict[str, dict[str, str]] = {}
		variables: dict[str, str] = {}
		for service_name, service in sorted(services.items()):
			image = service.get("image")
			build = service.get("build")
			if bool(image) == bool(build):
				raise ValueError(f"{service_name}: exactly one of image or build is required.")
			if image:
				image_match = _EXTERNAL_IMAGE_RE.fullmatch(image)
				variable_match = _IMAGE_VARIABLE_RE.fullmatch(image)
				if image_match:
					name, digest = image_match.groups()
					if name in images and images[name] != digest:
						raise ValueError(f"Conflicting digest for external image {name}.")
					images[name] = digest
				elif variable_match:
					variables[service_name] = variable_match.group(1)
				else:
					raise ValueError(f"{service_name}: image must be digest-pinned or a required runtime variable.")
				continue
			if not service.get("context"):
				raise ValueError(f"{service_name}: source build has no context.")
			context = _safe_archive_path(service["context"])
			dockerfile = _safe_archive_path(service.get("dockerfile", "Dockerfile"))
			dockerfile_path = f"{context}/{dockerfile}"
			if dockerfile_path not in members or not members[dockerfile_path].isfile():
				raise ValueError(f"{service_name}: archived Dockerfile is missing: {dockerfile_path}")
			prefix = context + "/"
			paths = sorted(
				path for path, member in members.items()
				if path.startswith(prefix) and (member.isfile() or member.issym())
			)
			if not paths:
				raise ValueError(f"{service_name}: archived build context is empty.")
			digest = hashlib.sha256()
			for path in paths:
				member = members[path]
				if member.isfile():
					handle = archive.extractfile(member)
					if handle is None:
						raise ValueError(f"Cannot read archived source: {path}")
					file_hash = hashlib.sha256()
					for chunk in iter(lambda: handle.read(1024 * 1024), b""):
						file_hash.update(chunk)
					content_hash = b"F" + file_hash.digest()
				else:
					content_hash = b"L" + member.linkname.encode("utf-8")
				digest.update(path.encode("utf-8") + b"\\0" + content_hash + b"\\0")
			builds[service_name] = {
				"context": context,
				"dockerfile": dockerfile_path,
				"source_sha256": digest.hexdigest(),
			}
	return images, builds, variables


def _validate_manifest_data(data: dict[str, Any], commit: str) -> None:
	if not _COMMIT_RE.fullmatch(commit):
		raise SystemExit("Release commit must be a full immutable Git SHA.")
	if data.get("schema_version") != 2:
		raise SystemExit("Unsupported release manifest schema.")
	if data.get("commit") != commit:
		raise SystemExit("Release manifest commit mismatch.")
	artifact_digest = str(data.get("artifact_sha256") or "")
	if not re.fullmatch(r"[0-9a-f]{64}", artifact_digest):
		raise SystemExit("Release artifact digest is invalid.")
	images = data.get("container_image_digests")
	builds = data.get("source_build_contexts")
	variables = data.get("runtime_image_variables")
	if not isinstance(images, dict) or not isinstance(builds, dict) or not isinstance(variables, dict):
		raise SystemExit("Release must inventory external, source-built, and runtime-variable images.")
	if not images or not builds:
		raise SystemExit("Release is missing external images or source-built service provenance.")
	for name, digest in images.items():
		if not str(name).strip() or not _DIGEST_RE.fullmatch(str(digest)):
			raise SystemExit(f"Invalid external image digest: {name}.")
	for service, entry in builds.items():
		if not isinstance(service, str) or not service or not isinstance(entry, dict):
			raise SystemExit("Invalid source build entry.")
		if set(entry) != {"context", "dockerfile", "source_sha256"}:
			raise SystemExit(f"Incomplete source build provenance for {service}.")
		if not re.fullmatch(r"[0-9a-f]{64}", str(entry["source_sha256"])):
			raise SystemExit(f"Invalid source build fingerprint for {service}.")
		for path in (entry["context"], entry["dockerfile"]):
			if not isinstance(path, str) or _safe_archive_path(path) != path:
				raise SystemExit(f"Invalid source build path for {service}.")
	for service, name in variables.items():
		if not isinstance(service, str) or not service or not re.fullmatch(r"[A-Z][A-Z0-9_]*", str(name)):
			raise SystemExit("Invalid required runtime image variable.")


def create(out: Path, artifact: Path, commit: str) -> None:
	root = Path(__file__).resolve().parents[2]
	actual_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
	if actual_commit != commit:
		raise SystemExit("Release commit does not match the checked-out Git revision.")
	images, builds, variables = _archive_inventory(artifact)
	payload = {
		"schema_version": 2,
		"commit": commit,
		"artifact": artifact.name,
		"artifact_sha256": sha256(artifact),
		"container_image_digests": images,
		"source_build_contexts": builds,
		"runtime_image_variables": variables,
		"created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
		"workflow_run_id": __import__("os").getenv("GITHUB_RUN_ID", ""),
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
	images, builds, variables = _archive_inventory(artifact)
	if data["container_image_digests"] != images:
		raise SystemExit("Release external image inventory mismatch.")
	if data["source_build_contexts"] != builds:
		raise SystemExit("Release source-build inventory mismatch.")
	if data["runtime_image_variables"] != variables:
		raise SystemExit("Release runtime image-variable inventory mismatch.")


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
