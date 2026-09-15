from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


class ModelArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelManifest:
    name: str
    filename: str
    relative_path: str
    url: str
    sha256: str
    upstream_md5: str


_MANIFEST_PATH = Path(__file__).with_name("model_manifest.json")
_DEFAULT_REMBG_HOME = Path("/opt/aos/rembg")
_HEX_64 = set("0123456789abcdef")
_HEX_32 = set("0123456789abcdef")


def _validate_hex(value: str, *, length: int, field: str) -> str:
    normalized = str(value or "").strip().lower()
    allowed = _HEX_64 if length == 64 else _HEX_32
    if len(normalized) != length or any(character not in allowed for character in normalized):
        raise ModelArtifactError(f"Invalid {field} in the bundled model manifest.")
    return normalized


@lru_cache(maxsize=1)
def get_model_manifest() -> ModelManifest:
    try:
        raw = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ModelArtifactError("Unable to read the bundled model manifest.") from exc

    required = {"name", "filename", "relative_path", "url", "sha256", "upstream_md5"}
    if set(raw) != required:
        raise ModelArtifactError("Bundled model manifest fields are invalid.")

    name = str(raw["name"] or "").strip()
    filename = str(raw["filename"] or "").strip()
    relative_path = str(raw["relative_path"] or "").strip()
    url = str(raw["url"] or "").strip()
    if name != "u2net" or filename != "u2net.onnx":
        raise ModelArtifactError("The background-removal image only supports the reviewed U2Net artifact.")
    if not relative_path or Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
        raise ModelArtifactError("Bundled model manifest path is invalid.")
    if relative_path != "models/u2net/u2net.onnx":
        raise ModelArtifactError("Bundled model manifest path does not match the rembg model layout.")
    if not url.startswith("https://github.com/danielgatis/rembg/releases/download/"):
        raise ModelArtifactError("Bundled model source is not the reviewed upstream release location.")

    return ModelManifest(
        name=name,
        filename=filename,
        relative_path=relative_path,
        url=url,
        sha256=_validate_hex(raw["sha256"], length=64, field="SHA-256"),
        upstream_md5=_validate_hex(raw["upstream_md5"], length=32, field="upstream MD5"),
    )


def rembg_home() -> Path:
    configured = str(os.getenv("REMBG_HOME") or "").strip()
    return Path(configured).expanduser() if configured else _DEFAULT_REMBG_HOME


def expected_model_path() -> Path:
    return rembg_home() / get_model_manifest().relative_path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model_artifact() -> Path:
    """Fail closed unless the exact reviewed, immutable model is present."""
    manifest = get_model_manifest()
    root = rembg_home()
    path = expected_model_path()

    try:
        root_resolved = root.resolve(strict=True)
        file_lstat = path.lstat()
    except FileNotFoundError as exc:
        raise ModelArtifactError("The bundled background-removal model is missing.") from exc

    if stat.S_ISLNK(file_lstat.st_mode):
        raise ModelArtifactError("The bundled background-removal model must not be a symlink.")
    if not stat.S_ISREG(file_lstat.st_mode):
        raise ModelArtifactError("The bundled background-removal model is not a regular file.")
    if file_lstat.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise ModelArtifactError("The bundled background-removal model must be read-only.")

    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root_resolved)
    except (FileNotFoundError, ValueError) as exc:
        raise ModelArtifactError("The bundled background-removal model escaped its immutable model root.") from exc

    if _sha256_file(resolved) != manifest.sha256:
        raise ModelArtifactError("The bundled background-removal model checksum is invalid.")

    return resolved
