from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from release_manifest import sha256, verify_manifest

_SHA = re.compile(r"^[0-9a-f]{64}$")
_OCI = re.compile(
    r"^(?P<name>[a-z0-9][a-z0-9.-]*(?::[0-9]+)?/[a-z0-9_.-]+(?:/[a-z0-9_.-]+)*)"
    r"(?:[:][a-z0-9_.-]+)?@sha256:(?P<digest>[0-9a-f]{64})$"
)
_SCHEMA_KEYS = {
    "schema_version",
    "commit",
    "release_manifest_sha256",
    "build_context_images",
    "runtime_image_refs",
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"Duplicate JSON key: {name}")
        result[name] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: expected one JSON object.")
    return value


def _image_ref(value: Any, *, expected_name: str | None = None) -> str:
    if not isinstance(value, str):
        raise ValueError("Image reference must be a string.")
    match = _OCI.fullmatch(value)
    if not match or len(set(match.group("digest"))) < 8:
        raise ValueError("Image must have a real-looking immutable OCI sha256 reference.")
    if expected_name is not None and match.group("name") != expected_name:
        raise ValueError(f"Published image repository does not match required context: {expected_name}")
    return value


def _expected(manifest: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    if manifest["schema_version"] != 2:
        raise ValueError("Image lock requires release manifest schema 2.")
    contexts: dict[str, str] = {}
    for service, entry in manifest["source_build_contexts"].items():
        context = entry["context"]
        fingerprint = entry["source_sha256"]
        previous = contexts.setdefault(context, fingerprint)
        if previous != fingerprint:
            raise ValueError(f"Conflicting build fingerprints for context: {context}")
        if not _SHA.fullmatch(fingerprint):
            raise ValueError(f"Invalid source fingerprint: {service}")
    return contexts, manifest["runtime_image_variables"]


def _validate(
    manifest_path: Path, lock: dict[str, Any], commit: str
) -> list[str]:
    manifest = verify_manifest(manifest_path, commit)
    contexts, runtime = _expected(manifest)
    if set(lock) != _SCHEMA_KEYS or lock["schema_version"] != 1:
        raise ValueError("Unsupported or incomplete release image lock.")
    if lock["commit"] != commit or lock["release_manifest_sha256"] != sha256(manifest_path):
        raise ValueError("Image lock does not match this exact release manifest.")
    images = lock["build_context_images"]
    refs = lock["runtime_image_refs"]
    if not isinstance(images, dict) or set(images) != set(contexts):
        raise ValueError("Image lock is missing or inventing a source-build context.")
    if not isinstance(refs, dict) or set(refs) != set(runtime):
        raise ValueError("Image lock is missing or inventing a runtime image service.")
    all_refs: list[str] = []
    for context, source in sorted(contexts.items()):
        entry = images[context]
        if not isinstance(entry, dict) or set(entry) != {"image_ref", "source_sha256"}:
            raise ValueError(f"{context}: malformed source-build image receipt.")
        if entry["source_sha256"] != source:
            raise ValueError(f"{context}: image receipt source fingerprint mismatch.")
        if not context.startswith("infra/"):
            raise ValueError("Source-build image context must be under infra/.")
        suffix = context.removeprefix("infra/").replace("/", "-")
        all_refs.append(
            _image_ref(
                entry["image_ref"],
                expected_name=f"ghcr.io/janja-programmers/aos-{suffix}",
            )
        )
    for service, image in sorted(refs.items()):
        all_refs.append(_image_ref(image))
    return sorted(set(all_refs))


def create(
    manifest_path: Path,
    build_receipts: Path,
    runtime_receipts: Path,
    out: Path,
    commit: str,
) -> None:
    manifest = verify_manifest(manifest_path, commit)
    contexts, runtime = _expected(manifest)
    images = _read_json(build_receipts)
    refs = _read_json(runtime_receipts)
    if set(images) != set(contexts) or set(refs) != set(runtime):
        raise ValueError("Published image receipts must cover every source context and runtime image.")
    lock = {
        "schema_version": 1,
        "commit": commit,
        "release_manifest_sha256": sha256(manifest_path),
        "build_context_images": {
            context: {"image_ref": images[context], "source_sha256": contexts[context]}
            for context in sorted(contexts)
        },
        "runtime_image_refs": refs,
    }
    _validate(manifest_path, lock, commit)
    out.write_text(json.dumps(lock, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def verify(manifest_path: Path, lock_path: Path, commit: str, *, registry: bool) -> None:
    refs = _validate(manifest_path, _read_json(lock_path), commit)
    if registry:
        for reference in refs:
            result = subprocess.run(
                ["crane", "digest", reference],
                text=True,
                capture_output=True,
                check=False,
                timeout=90,
            )
            if result.returncode or result.stdout.strip() != "sha256:" + reference.rsplit("@sha256:", 1)[1]:
                raise ValueError(f"Registry cannot verify immutable image: {reference.split('@', 1)[0]}")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "create" and len(sys.argv) == 7:
        create(*(Path(argument) for argument in sys.argv[2:6]), sys.argv[6])
    elif command in {"verify", "verify-registry"} and len(sys.argv) == 5:
        verify(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], registry=command == "verify-registry")
    else:
        raise SystemExit(
            "usage: image_lock.py create MANIFEST BUILD_RECEIPTS RUNTIME_REFS OUT COMMIT "
            "| verify[ -registry] MANIFEST LOCK COMMIT"
        )


if __name__ == "__main__":
    main()
