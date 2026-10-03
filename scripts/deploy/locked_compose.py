"""Render and check a no-build Compose deployment from the exact promoted release."""

from __future__ import annotations

import re
import sys
import tarfile
from pathlib import Path
from typing import Any

import yaml

from image_lock import _read_json, _validate, verify as verify_image_lock
from release_manifest import _archive_inventory, verify as verify_release, verify_manifest

_STATIC_IMAGE = re.compile(r"^([^\s@]+)@(sha256:[0-9a-f]{64})$")


class UniqueMappingLoader(yaml.SafeLoader):
    """Reject duplicate Compose keys instead of silently accepting a replacement."""


def _unique_mapping(loader: UniqueMappingLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in result:
            raise ValueError(f"Duplicate Compose mapping key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


UniqueMappingLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def build_compose(
    manifest_path: Path,
    artifact: Path,
    lock_path: Path,
    commit: str,
    *,
    registry: bool = False,
) -> dict[str, Any]:
    verify_release(manifest_path, artifact, commit)
    image_lock = _read_json(lock_path)
    _validate(manifest_path, image_lock, commit)
    if registry:
        verify_image_lock(manifest_path, lock_path, commit, registry=True)
    manifest = verify_manifest(manifest_path, commit)
    with tarfile.open(artifact, "r:gz") as archive:
        member = archive.getmember("docker-compose.yml")
        handle = archive.extractfile(member)
        if handle is None:
            raise ValueError("Release Compose source is missing.")
        source = handle.read().decode("utf-8")
    compose = yaml.load(source, Loader=UniqueMappingLoader)
    if not isinstance(compose, dict) or "include" in compose:
        raise ValueError("Included or incomplete Compose sources are not permitted.")
    services = compose.get("services")
    if not isinstance(services, dict) or not services:
        raise ValueError("Release Compose must declare all required services.")
    _, declared_builds, declared_runtime = _archive_inventory(artifact)
    actual_builds: set[str] = set()
    actual_runtime: set[str] = set()
    for name, service in services.items():
        if not isinstance(service, dict) or "extends" in service:
            raise ValueError(f"{name}: unsupported Compose service extension.")
        if "build" in service:
            if name not in declared_builds or "image" in service:
                raise ValueError(f"{name}: unrecognized or ambiguous source build.")
            origin = declared_builds[name]["context"]
            image = image_lock["build_context_images"][origin]["image_ref"]
            service.pop("build")
            service["image"] = image
            actual_builds.add(name)
        elif name in declared_runtime:
            if "image" not in service:
                raise ValueError(f"{name}: required runtime image is missing.")
            service["image"] = image_lock["runtime_image_refs"][name]
            actual_runtime.add(name)
        else:
            value = service.get("image")
            match = _STATIC_IMAGE.fullmatch(value if isinstance(value, str) else "")
            if not match:
                raise ValueError(f"{name}: static service has no immutable image reference.")
            repository, digest = match.groups()
            if manifest["container_image_digests"].get(repository) != digest:
                raise ValueError(f"{name}: static image differs from release manifest.")
        if "build" in service or "@sha256:" not in service["image"]:
            raise ValueError(f"{name}: a build or mutable image escaped release locking.")
    if actual_builds != set(declared_builds) or actual_runtime != set(declared_runtime):
        raise ValueError("Rendered release does not cover every declared source or runtime image.")
    return compose


def main() -> None:
    if len(sys.argv) != 7 or sys.argv[1] not in {"render-offline", "render-registry", "verify-offline", "verify-registry"}:
        raise SystemExit(
            "usage: locked_compose.py render-offline|render-registry|verify-offline|verify-registry "
            "MANIFEST ARCHIVE IMAGE_LOCK COMMIT OUTPUT_COMPOSE"
        )
    mode, manifest, archive, lock, commit, output = sys.argv[1:]
    expected = build_compose(
        Path(manifest), Path(archive), Path(lock), commit,
        registry=mode.endswith("registry"),
    )
    target = Path(output)
    if mode.startswith("render-"):
        target.write_text(yaml.safe_dump(expected, sort_keys=False), encoding="utf-8")
    else:
        actual = yaml.load(target.read_text(encoding="utf-8"), Loader=UniqueMappingLoader)
        if actual != expected:
            raise ValueError("Deployment Compose differs from the exact promoted image lock.")


if __name__ == "__main__":
    main()
