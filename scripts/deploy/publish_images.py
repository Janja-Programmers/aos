"""Manually publish the archived AOS build contexts with immutable image receipts.

This command must run only in the reviewed image-promotion GitHub Environment.
It never contacts application deployment hosts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from image_lock import _image_ref, create as create_lock, verify as verify_lock
from promotion_plan import verify_plan

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _run(args: list[str], *, capture: bool = True) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def _verified_digest(reference: str, expected: str) -> None:
    if not _DIGEST.fullmatch(expected):
        raise ValueError("Build output does not contain a valid OCI manifest digest.")
    actual = _run(["crane", "digest", reference])
    if actual != expected:
        raise ValueError("Registry digest differs from the trusted build output.")


def _verify_labels(reference: str, *, commit: str, context: str, fingerprint: str) -> None:
    configuration = json.loads(_run(["crane", "config", reference]))
    labels = configuration.get("config", {}).get("Labels") or {}
    required = {
        "org.opencontainers.image.revision": commit,
        "org.opencontainers.image.source": "https://github.com/Janja-Programmers/aos",
        "io.aos.release.context": context,
        "io.aos.release.source-sha256": fingerprint,
    }
    if not isinstance(labels, dict) or any(labels.get(key) != value for key, value in required.items()):
        raise ValueError(f"Published image labels do not match the verified release: {context}")


def _runtime_receipts(plan: dict[str, Any]) -> dict[str, str]:
    expected = plan["runtime_image_variables"]
    receipts = {}
    for service, variable in sorted(expected.items()):
        value = os.environ.get(variable, "")
        receipts[service] = _image_ref(value)
    return receipts


def publish(
    manifest: Path,
    artifact: Path,
    plan_path: Path,
    output: Path,
    commit: str,
) -> None:
    verified = verify_plan(manifest, artifact, plan_path, commit)
    if os.getenv("AOS_IMAGE_PROMOTION_ENABLED") != "true":
        raise ValueError("Protected image promotion is disabled.")
    if os.getenv("CI") != "true" or os.getenv("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Publishing is only permitted from the protected main workflow.")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    runtime = _runtime_receipts(verified)
    with tempfile.TemporaryDirectory(prefix="aos-image-build-") as directory:
        checkout = Path(directory)
        with tarfile.open(artifact, "r:gz") as archive:
            archive.extractall(checkout, filter="data")
        receipts: dict[str, str] = {}
        for build in verified["builds"]:
            context = build["context"]
            repository = build["image_repository"]
            tag = build["image_tag"]
            fingerprint = build["source_sha256"]
            metadata_path = checkout / f".aos-build-metadata-{len(receipts)}.json"
            _run(
                [
                    "docker", "buildx", "build",
                    "--pull",
                    "--platform", "linux/amd64",
                    "--provenance=mode=max",
                    "--sbom=true",
                    "--label", f"org.opencontainers.image.revision={commit}",
                    "--label", "org.opencontainers.image.source=https://github.com/Janja-Programmers/aos",
                    "--label", f"io.aos.release.context={context}",
                    "--label", f"io.aos.release.source-sha256={fingerprint}",
                    "--file", str(checkout / build["dockerfile"]),
                    "--tag", tag,
                    "--metadata-file", str(metadata_path),
                    "--push",
                    str(checkout / context),
                ],
                capture=False,
            )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            digest = metadata.get("containerimage.digest")
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise ValueError(f"{context}: trusted builder did not return a manifest digest.")
            _verified_digest(tag, digest)
            reference = _image_ref(f"{repository}@{digest}", expected_name=repository)
            _verify_labels(reference, commit=commit, context=context, fingerprint=fingerprint)
            receipts[context] = reference

    build_path = output / "build-receipts.json"
    runtime_path = output / "runtime-receipts.json"
    lock_path = output / "release-image-lock.json"
    build_path.write_text(json.dumps(receipts, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    runtime_path.write_text(json.dumps(runtime, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    create_lock(manifest, build_path, runtime_path, lock_path, commit)
    verify_lock(manifest, lock_path, commit, registry=True)
    print("Immutable OCI image promotion completed with full registry verification.")


def main() -> None:
    if len(sys.argv) != 7 or sys.argv[1] != "publish":
        raise SystemExit("usage: publish_images.py publish MANIFEST ARTIFACT PLAN OUTPUT_DIRECTORY COMMIT")
    publish(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), Path(sys.argv[5]), sys.argv[6])


if __name__ == "__main__":
    main()
