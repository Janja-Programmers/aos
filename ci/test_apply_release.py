"""Offline host-applier acceptance tests. No Docker, SSH, registry or real Bench involved."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("apply_release", ROOT / "scripts/deploy/apply-release.py")
assert SPEC is not None and SPEC.loader is not None
applier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(applier)
COMMIT = "a" * 40
IMAGE = "ghcr.io/janja-programmers/aos-translation@sha256:" + "1" * 64


class HostApplierTests(unittest.TestCase):
	def setUp(self) -> None:
		self.temporary = tempfile.TemporaryDirectory()
		self.addCleanup(self.temporary.cleanup)
		self.root = Path(self.temporary.name)
		self.release_root = self.root / "releases"
		self.release = self.release_root / COMMIT
		(self.release / "policy").mkdir(parents=True)
		self.project = self.root / "runtime"
		self.project.mkdir()
		(self.project / ".env").write_text("APP_READY=1\n", encoding="utf-8")
		(self.project / ".env").chmod(0o600)
		self.bench = self.root / "bench"
		(self.bench / "apps").mkdir(parents=True)
		(self.bench / "env/bin").mkdir(parents=True)
		self.python = self.bench / "env/bin/python"
		self.python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
		self.python.chmod(0o700)
		self.previous = self.root / "previous"
		self.previous.mkdir()
		self.link = self.bench / "apps/aos"
		self.link.symlink_to(self.previous, target_is_directory=True)
		self.archive = self.release / "aos-release.tar.gz"
		self.members: dict[str, bytes] = {
			"docker-compose.yml": b"services:\n  example:\n    image: pinned\n",
			"aos/__init__.py": b"APP_VERSION = 1\n",
			"scripts/deploy/release_manifest.py": b"# manifest policy\n",
			"scripts/deploy/image_lock.py": b"# lock policy\n",
			"scripts/deploy/locked_compose.py": b"# compose policy\n",
		}
		with tarfile.open(self.archive, "w:gz") as archive:
			for name, data in self.members.items():
				info = tarfile.TarInfo(name)
				info.size = len(data)
				info.mode = 0o755 if name.startswith("scripts/") else 0o644
				archive.addfile(info, io.BytesIO(data))
		for name in applier.POLICIES:
			(self.release / "policy" / name).write_bytes(self.members[f"scripts/deploy/{name}"])
		self.manifest = self.release / "release-manifest.json"
		self.manifest.write_text("{}", encoding="utf-8")
		self.lock = self.release / "release-image-lock.json"
		self.lock.write_text("{}", encoding="utf-8")
		self.compose = self.release / "release-compose.locked.yml"
		self.compose.write_text(f"services:\n  example:\n    image: {IMAGE}\n", encoding="utf-8")
		self.arguments = [
			str(self.archive),
			str(self.manifest),
			COMMIT,
			str(self.lock),
			str(self.compose),
		]
		self.settings = mock.patch.dict(
			os.environ,
			{
				"REMOTE_BENCH_ROOT": str(self.bench),
				"REMOTE_RELEASE_ROOT": str(self.release_root),
				"REMOTE_PROJECT_ROOT": str(self.project),
			},
		)
		self.settings.start()
		self.addCleanup(self.settings.stop)
		self.commands: list[list[str]] = []

	def fake_run(self, command: list[str], *, capture: bool = False) -> str:
		self.commands.append(command)
		if command[0] == "docker" and "config" in command:
			return json.dumps({"services": {"example": {"image": IMAGE}}})
		if "-c" in command:
			return json.dumps({"example": self.compose.read_text().split("image: ", 1)[1].strip()})
		return ""

	def test_accepts_exact_lock_and_switches_application_atomically(self) -> None:
		with mock.patch.object(applier, "run", side_effect=self.fake_run):
			applier.apply(self.arguments)
			self.assertEqual(self.link.resolve(), self.release / "source")
			self.assertEqual((self.link / "aos/__init__.py").read_bytes(), self.members["aos/__init__.py"])
			self.assertTrue(any("pull" in command for command in self.commands))
			self.assertTrue(any("up" in command and "--no-build" in command for command in self.commands))
			self.assertFalse(any("build" in command or "down" in command for command in self.commands))
			applier.apply(self.arguments)
			self.assertEqual(self.link.resolve(), self.release / "source")

	def test_mutated_extracted_source_fails_before_pull(self) -> None:
		with mock.patch.object(applier, "run", side_effect=self.fake_run):
			applier.apply(self.arguments)
			(self.release / "source/aos/__init__.py").write_text("changed\n", encoding="utf-8")
			self.commands.clear()
			with self.assertRaisesRegex(ValueError, "source was modified"):
				applier.apply(self.arguments)
			self.assertFalse(any("pull" in command for command in self.commands))

	def test_mutable_image_fails_without_activation(self) -> None:
		self.compose.write_text("services:\n  example:\n    image: example:latest\n", encoding="utf-8")
		with mock.patch.object(applier, "run", side_effect=self.fake_run):
			with self.assertRaisesRegex(ValueError, "mutable image"):
				applier.apply(self.arguments)
		self.assertEqual(self.link.resolve(), self.previous)
		self.assertFalse(any("pull" in command for command in self.commands))

	def test_missing_host_onboarding_fails_closed(self) -> None:
		self.link.unlink()
		with mock.patch.object(applier, "run", side_effect=self.fake_run):
			with self.assertRaisesRegex(ValueError, "reviewed symlink"):
				applier.apply(self.arguments)
		self.assertFalse(self.commands)

	def test_rejects_input_symlink_and_wrong_arity(self) -> None:
		with self.assertRaisesRegex(ValueError, "usage"):
			applier.apply(self.arguments[:3])
		self.manifest.rename(self.release / "actual.json")
		self.manifest.symlink_to(self.release / "actual.json")
		with mock.patch.object(applier, "run", side_effect=self.fake_run):
			with self.assertRaisesRegex(ValueError, "linked"):
				applier.apply(self.arguments)
		self.assertFalse(self.commands)

	def test_rejects_archive_traversal_and_symlinks(self) -> None:
		for unsafe in ("../outside", "/absolute", "nested/../escape", "duplicate"):
			with self.subTest(unsafe=unsafe):
				with tempfile.TemporaryFile() as temporary:
					with tarfile.open(fileobj=temporary, mode="w") as archive:
						info = tarfile.TarInfo(unsafe)
						info.size = 1
						archive.addfile(info, io.BytesIO(b"x"))
						if unsafe == "duplicate":
							archive.addfile(info, io.BytesIO(b"x"))
					temporary.seek(0)
					with tarfile.open(fileobj=temporary, mode="r") as archive:
						with self.assertRaises(ValueError):
							applier.archive_inventory(archive)
		with tempfile.TemporaryFile() as temporary:
			with tarfile.open(fileobj=temporary, mode="w") as archive:
				link = tarfile.TarInfo("unsafe-link")
				link.type = tarfile.SYMTYPE
				link.linkname = "../../outside"
				archive.addfile(link)
			temporary.seek(0)
			with tarfile.open(fileobj=temporary, mode="r") as archive:
				with self.assertRaises(ValueError):
					applier.archive_inventory(archive)


if __name__ == "__main__":
	unittest.main()
