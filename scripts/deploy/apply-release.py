#!/usr/bin/env python3
"""Reviewed host applier: exactly five immutable inputs, never rebuild or restore data."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
IMAGE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}\Z")
PATH = re.compile(r"/[A-Za-z0-9_./-]+\Z")
POLICIES = ("release_manifest.py", "image_lock.py", "locked_compose.py")


def refuse(message: str) -> None:
    raise ValueError(message)


def host_path(name: str) -> Path:
    value = os.environ.get(name, "")
    if not PATH.fullmatch(value) or ".." in value:
        refuse(f"Missing or invalid reviewed host setting: {name}")
    target = Path(value)
    if target.is_symlink() or not target.is_dir():
        refuse(f"Host directory is missing or symlinked: {name}")
    return target


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode:
        # Never echo Compose stderr: failed config may contain interpolated secrets.
        refuse(f"Required release command failed ({command[0]}).")
    if not capture and result.stdout:
        print(result.stdout, end="")
    return result.stdout


def archive_inventory(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    seen: set[str] = set()
    total_size = 0
    for member in archive:
        name = member.name.removeprefix("./").rstrip("/")
        parts = PurePosixPath(name).parts
        if (
            not name
            or name.startswith("/")
            or any(part in ("", ".", "..") for part in name.split("/"))
            or not parts
            or name in seen
            or not (member.isfile() or member.isdir())
        ):
            refuse("Release archive contains duplicate, unsafe or linked source entries.")
        seen.add(name)
        total_size += member.size
        if len(seen) > 10000 or total_size > 2 * 1024**3:
            refuse("Release extraction limits exceeded.")
        members.append(member)
    return members


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_source(
    archive: tarfile.TarFile, members: list[tarfile.TarInfo], release: Path
) -> Path:
    source = release / "source"
    if source.exists() or source.is_symlink():
        if not source.is_dir() or source.is_symlink():
            refuse("Previously extracted release source is not a regular directory.")
        for member in members:
            name = member.name.removeprefix("./").rstrip("/")
            target = source.joinpath(*PurePosixPath(name).parts)
            if member.isdir():
                if not target.is_dir() or target.is_symlink():
                    refuse("Previously extracted release directory was modified.")
            else:
                handle = archive.extractfile(member)
                if (
                    handle is None
                    or not target.is_file()
                    or target.is_symlink()
                    or sha256(handle.read()) != sha256(target.read_bytes())
                ):
                    refuse("Previously extracted release source was modified.")
        return source

    stage = Path(tempfile.mkdtemp(prefix=".source-", dir=release))
    try:
        for member in members:
            name = member.name.removeprefix("./").rstrip("/")
            target = stage.joinpath(*PurePosixPath(name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            handle = archive.extractfile(member)
            if handle is None:
                refuse("Release source file is unreadable.")
            # Exclusive creation and verified regular archive members prevent tar
            # extraction of device files, links, ownership and traversal.
            with target.open("xb") as output:
                shutil.copyfileobj(handle, output)
            target.chmod(member.mode & 0o755 | 0o400)
        stage.rename(source)
        return source
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def verify_policy(archive: tarfile.TarFile, release: Path) -> None:
    policy_dir = release / "policy"
    if not policy_dir.is_dir() or policy_dir.is_symlink():
        refuse("The trusted remote policy directory is missing.")
    for name in POLICIES:
        handle = archive.extractfile(f"scripts/deploy/{name}")
        trusted = policy_dir / name
        if (
            handle is None
            or not trusted.is_file()
            or trusted.is_symlink()
            or sha256(handle.read()) != sha256(trusted.read_bytes())
        ):
            refuse("Remote release validator differs from verified archive.")


def apply(inputs: list[str]) -> None:
    if len(inputs) != 5:
        refuse("usage: aos-apply-release ARCHIVE MANIFEST COMMIT IMAGE_LOCK LOCKED_COMPOSE")
    archive_path, manifest, commit, image_lock, locked_compose = inputs
    if not COMMIT.fullmatch(commit):
        refuse("Release must be an exact immutable Git commit.")

    release_root = host_path("REMOTE_RELEASE_ROOT")
    bench_root = host_path("REMOTE_BENCH_ROOT")
    project_root = host_path("REMOTE_PROJECT_ROOT")
    release = release_root / commit
    if not release.is_dir() or release.is_symlink():
        refuse("The expected release directory is unavailable.")
    expected = (
        release / "aos-release.tar.gz",
        release / "release-manifest.json",
        release / "release-image-lock.json",
        release / "release-compose.locked.yml",
    )
    for supplied, canonical in zip(
        (archive_path, manifest, image_lock, locked_compose), expected, strict=True
    ):
        if supplied != str(canonical) or not canonical.is_file() or canonical.is_symlink():
            refuse("Release input is missing, linked, or not at its canonical path.")

    bench_python = bench_root / "env/bin/python"
    bench_app = bench_root / "apps/aos"
    env_file = project_root / ".env"
    if not bench_python.is_file() or not os.access(bench_python, os.X_OK):
        refuse("Pinned Bench Python is unavailable.")
    if not bench_app.is_symlink():
        refuse("Bench apps/aos must first be provisioned as a reviewed symlink.")
    if (
        not env_file.is_file()
        or env_file.is_symlink()
        or env_file.stat().st_mode & 0o077
    ):
        refuse("Compose credentials must be in a non-symlink host .env with mode 0600.")

    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive_inventory(archive)
        verify_policy(archive, release)
        run(
            [
                str(bench_python),
                str(release / "policy/locked_compose.py"),
                "verify-offline",
                manifest,
                archive_path,
                image_lock,
                commit,
                locked_compose,
            ]
        )

        compose = [
            "docker",
            "compose",
            "--project-directory",
            str(project_root),
            "--env-file",
            str(env_file),
            "-f",
            locked_compose,
        ]
        configured = json.loads(run([*compose, "config", "--format", "json"], capture=True))
        expected_images = json.loads(
            run(
                [
                    str(bench_python),
                    "-c",
                    "import json,sys,yaml; s=yaml.safe_load(open(sys.argv[1], encoding='utf-8'));"
                    " print(json.dumps({n:v['image'] for n,v in s['services'].items()}))",
                    locked_compose,
                ],
                capture=True,
            )
        )
        services = configured.get("services")
        if not isinstance(services, dict) or set(services) != set(expected_images):
            refuse("Compose configuration omits or invents release services.")
        for name, image in expected_images.items():
            if not IMAGE.fullmatch(image):
                refuse("A mutable image escaped the approved Compose.")
            if services[name].get("image") != image or "build" in services[name]:
                refuse("Compose changed an approved image or introduced a build.")

        # Serialize independent deploy and rollback appliers on each target host.
        lock = release_root / ".activation.lock"
        if lock.is_symlink():
            refuse("Host activation lock cannot be a symlink.")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            source = extract_source(archive, members, release)
            run([*compose, "pull", "--policy", "always"])
            run(
                [
                    *compose,
                    "up",
                    "--detach",
                    "--no-build",
                    "--pull",
                    "never",
                    "--wait",
                    "--wait-timeout",
                    "420",
                ]
            )

            # Switch source only after the pinned images are healthy. No database
            # migration runs here: rollback uses the identical applier.
            fd_link, candidate_name = tempfile.mkstemp(prefix=".aos-next-", dir=bench_app.parent)
            os.close(fd_link)
            candidate = Path(candidate_name)
            candidate.unlink()
            try:
                candidate.symlink_to(source, target_is_directory=True)
                if not bench_app.is_symlink():
                    refuse("Bench app link changed before activation.")
                candidate.replace(bench_app)
            finally:
                if candidate.is_symlink():
                    candidate.unlink()
        finally:
            os.close(fd)
    print(f"[apply-release] Activated verified release {commit}; guarded migration is external.")


def main() -> int:
    try:
        apply(sys.argv[1:])
    except (ValueError, OSError, json.JSONDecodeError, tarfile.TarError, BlockingIOError) as exc:
        print(f"[apply-release] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
