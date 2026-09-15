from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "infra" / "background-removal"
SHA256 = "8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491"
MODEL_PATH = "models/u2net/u2net.onnx"


def fail(message: str, failures: list[str]) -> None:
    failures.append(message)


def main() -> int:
    failures: list[str] = []
    dockerfile = (SERVICE / "Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["background-removal"]
    manifest = json.loads((SERVICE / "app" / "model_manifest.json").read_text(encoding="utf-8"))
    requirements = (SERVICE / "requirements.txt").read_text(encoding="utf-8")
    lock = (SERVICE / "requirements.lock").read_text(encoding="utf-8")

    versions: dict[str, str] = {}
    for raw in (ROOT / "ci" / "versions.env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        versions[key] = value

    expected_from = f"FROM {versions['BACKGROUND_REMOVAL_PYTHON_IMAGE']}"
    if expected_from not in dockerfile:
        fail("background-removal Dockerfile does not use the pinned dedicated Python image", failures)
    if "REMBG_HOME=/opt/aos/rembg" not in dockerfile:
        fail("background-removal image does not pin REMBG_HOME", failures)
    if "NUMBA_CACHE_DIR=/var/cache/aos/numba" not in dockerfile:
        fail("background-removal image does not pin a writable Numba cache path", failures)
    if manifest.get("sha256") != SHA256 or manifest.get("relative_path") != MODEL_PATH:
        fail("background-removal model manifest does not match the reviewed artifact", failures)
    if SHA256 not in json.dumps(manifest, sort_keys=True):
        fail("background-removal model SHA-256 pin is missing", failures)
    if "rembg[cpu]==2.0.84" not in requirements:
        fail("background-removal does not pin the REMBG_HOME-capable rembg release", failures)
    if "rembg==2.0.84" not in lock:
        fail("background-removal production lock does not contain rembg 2.0.84", failures)
    for package_hash in (
        "3f27854a35b2e65aa74cc144040423f701a1a7bf8031359e2fb7a5e00ec355fe",
        "f180a7443c8552d1d45b15464a8266f6b585ec39fbeb6883899da8037bbeef0b",
    ):
        if package_hash not in lock:
            fail("background-removal rembg lock hashes do not match the reviewed release", failures)
    for required_dependency in (
        "jsonschema==",
        "numpy==",
        "onnxruntime==",
        "pillow==",
        "pooch==",
        "pymatting==",
        "scikit-image==",
        "scipy==",
        "tqdm==",
    ):
        if required_dependency not in lock.lower():
            fail(f"background-removal production lock is missing {required_dependency[:-2]}", failures)
    if "COPY app/model_manifest.json" not in dockerfile or "SHA-256 verification failed" not in dockerfile:
        fail("background-removal image does not verify the bundled model during build", failures)
    if "chmod -R a-w /opt/aos/rembg" not in dockerfile:
        fail("background-removal image does not make the model root immutable", failures)

    if service.get("read_only") is not True:
        fail("background-removal root filesystem is not read-only", failures)
    if service.get("cap_drop") != ["ALL"]:
        fail("background-removal does not drop all Linux capabilities", failures)
    tmpfs = service.get("tmpfs") or []
    if not any(str(item).startswith("/var/cache/aos/numba:") for item in tmpfs):
        fail("background-removal Numba cache is not ephemeral tmpfs", failures)
    if not any(str(item).startswith("/tmp:") for item in tmpfs):
        fail("background-removal /tmp is not ephemeral tmpfs", failures)
    if service.get("volumes"):
        fail("background-removal must not use mutable model/runtime volumes", failures)
    health = json.dumps(service.get("healthcheck") or {})
    if "/ready" not in health:
        fail("background-removal Docker healthcheck is not readiness-backed", failures)
    environment = service.get("environment") or {}
    deprecated_home = "U2NET" + "_HOME"
    implementation_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            SERVICE / "Dockerfile",
            SERVICE / "app" / "config.py",
            SERVICE / "app" / "model_artifact.py",
            ROOT / ".env.example",
            ROOT / "docker-compose.yml",
        )
    )
    if deprecated_home in implementation_text:
        fail("background-removal implementation still uses the deprecated model-home variable", failures)
    if "BACKGROUND_REMOVAL_MODEL_NAME" in environment:
        fail("background-removal still exposes a runtime model selector", failures)
    for required in (
        "BACKGROUND_REMOVAL_MAX_CONCURRENT_INFERENCES",
        "BACKGROUND_REMOVAL_INFERENCE_ACQUIRE_TIMEOUT_SECONDS",
    ):
        if required not in environment:
            fail(f"background-removal is missing {required}", failures)

    volumes = compose.get("volumes") or {}
    if "background_removal_models" in volumes:
        fail("legacy mutable background-removal model volume still exists", failures)

    if re.search(r"FROM\s+python:3\.14", dockerfile):
        fail("background-removal still uses the repository Python 3.14 runtime", failures)

    if failures:
        print("Background-removal production runtime validation failed:", file=sys.stderr)
        for message in failures:
            print(f"- {message}", file=sys.stderr)
        return 1

    print("Background-removal immutable/offline runtime contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
