"""Derive a deterministic OCI promotion plan from the exact release archive."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from release_manifest import sha256, verify, verify_manifest

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_CONTEXT_RE = re.compile(r"^infra/[a-z0-9_-]+(?:/[a-z0-9_-]+)*$")


def plan(manifest_path: Path, artifact_path: Path, commit: str) -> dict[str, Any]:
    if not _COMMIT_RE.fullmatch(commit):
        raise ValueError("Promotion requires the exact immutable release commit.")
    verify(manifest_path, artifact_path, commit)
    manifest = verify_manifest(manifest_path, commit)
    contexts: dict[str, dict[str, str]] = {}
    for service, source in sorted(manifest["source_build_contexts"].items()):
        context = source["context"]
        if not _CONTEXT_RE.fullmatch(context):
            raise ValueError(f"{service}: unsupported build-context identity.")
        fingerprint = source["source_sha256"]
        dockerfile = source["dockerfile"]
        if dockerfile != f"{context}/Dockerfile":
            raise ValueError(f"{service}: build Dockerfile must be canonical.")
        if context in contexts and contexts[context]["source_sha256"] != fingerprint:
            raise ValueError(f"{service}: conflicting shared build-context fingerprint.")
        contexts[context] = {"source_sha256": fingerprint, "dockerfile": dockerfile}
    if not contexts:
        raise ValueError("Promotion cannot omit the application build contexts.")
    builds = []
    for context, source in sorted(contexts.items()):
        suffix = context.removeprefix("infra/").replace("/", "-")
        repository = f"ghcr.io/janja-programmers/aos-{suffix}"
        builds.append(
            {
                "context": context,
                "dockerfile": source["dockerfile"],
                "source_sha256": source["source_sha256"],
                "image_repository": repository,
                "image_tag": f"{repository}:sha-{commit}",
            }
        )
    return {
        "schema_version": 1,
        "commit": commit,
        "artifact_sha256": sha256(artifact_path),
        "release_manifest_sha256": sha256(manifest_path),
        "builds": builds,
        "runtime_image_variables": manifest["runtime_image_variables"],
    }


def write_plan(manifest: Path, artifact: Path, output: Path, commit: str) -> None:
    output.write_text(json.dumps(plan(manifest, artifact, commit), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify_plan(manifest: Path, artifact: Path, source: Path, commit: str) -> dict[str, Any]:
    expected = plan(manifest, artifact, commit)
    actual = json.loads(source.read_text(encoding="utf-8"))
    if actual != expected:
        raise ValueError("Promotion plan does not match the immutable release source.")
    return expected


def main() -> None:
    if len(sys.argv) != 6 or sys.argv[1] not in {"create", "verify"}:
        raise SystemExit("usage: promotion_plan.py create|verify MANIFEST ARTIFACT PLAN COMMIT")
    command, manifest, artifact, output, commit = sys.argv[1:]
    if command == "create":
        write_plan(Path(manifest), Path(artifact), Path(output), commit)
    else:
        verify_plan(Path(manifest), Path(artifact), Path(output), commit)


if __name__ == "__main__":
    main()
