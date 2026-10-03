"""Offline tests for the cross-command host deployment/rollback transaction."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "scripts/deploy/lib.sh"


class DeploymentTransactionTests(unittest.TestCase):
	def setUp(self) -> None:
		self.temporary = tempfile.TemporaryDirectory()
		self.addCleanup(self.temporary.cleanup)
		self.root = Path(self.temporary.name) / "releases"
		self.root.mkdir()
		self.env = {**os.environ, "REMOTE_RELEASE_ROOT": str(self.root)}
		self.entry = 'source "$1"; remote() { bash -Eeuo pipefail -c "$1"; }; remote_transaction "$2"'

	def command(self, source: str) -> list[str]:
		return ["bash", "-Eeuo", "pipefail", "-c", self.entry, "host-test", str(LIB), source]

	def test_nonblocking_lock_spans_activation_migration_and_restart(self) -> None:
		entered = self.root / "entered"
		finished = self.root / "finished"
		script = f"printf entered > '{entered}'; sleep 1; printf finished > '{finished}'"
		first = subprocess.Popen(
			self.command(script),
			cwd=ROOT,
			env=self.env,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
		)
		try:
			deadline = time.monotonic() + 5
			while not entered.exists() and first.poll() is None and time.monotonic() < deadline:
				time.sleep(0.02)
			self.assertTrue(entered.is_file(), "The first host transaction never acquired the lock.")
			competing = self.root / "competing"
			second = subprocess.run(
				self.command(f"printf bad > '{competing}'"),
				cwd=ROOT,
				env=self.env,
				capture_output=True,
				text=True,
				check=False,
			)
			self.assertEqual(second.returncode, 75, second.stderr)
			self.assertFalse(competing.exists(), "A competing rollback entered the active transaction.")
			stdout, stderr = first.communicate(timeout=5)
			self.assertEqual(first.returncode, 0, stdout + stderr)
			self.assertTrue(finished.exists())
			third = subprocess.run(
				self.command(f"printf accepted > '{competing}'"),
				cwd=ROOT,
				env=self.env,
				capture_output=True,
				text=True,
				check=False,
			)
			self.assertEqual(third.returncode, 0, third.stderr)
			self.assertEqual(competing.read_text(), "accepted")
		finally:
			if first.poll() is None:
				first.kill()
				first.communicate()

	def test_failed_activation_prevents_migration_and_unlocks(self) -> None:
		marker = self.root / "unexpected-migration"
		failed = subprocess.run(
			self.command(f"false && printf migrated > '{marker}'"),
			cwd=ROOT,
			env=self.env,
			capture_output=True,
			text=True,
			check=False,
		)
		self.assertNotEqual(failed.returncode, 0)
		self.assertFalse(marker.exists())
		retry = subprocess.run(
			self.command(f"printf accepted > '{marker}'"),
			cwd=ROOT,
			env=self.env,
			capture_output=True,
			text=True,
			check=False,
		)
		self.assertEqual(retry.returncode, 0, retry.stderr)

	def test_live_wrappers_share_single_host_transaction(self) -> None:
		deploy = (ROOT / "scripts/deploy/deploy.sh").read_text(encoding="utf-8")
		rollback = (ROOT / "scripts/deploy/rollback.sh").read_text(encoding="utf-8")
		smoke = (ROOT / "scripts/deploy/smoke.sh").read_text(encoding="utf-8")
		for name, script in (("deploy", deploy), ("rollback", rollback), ("smoke", smoke)):
			with self.subTest(name=name):
				self.assertEqual(script.count('remote_transaction "'), 1)
				self.assertNotIn("flock -w", script)
				self.assertNotIn("|| true", script)
		self.assertLess(
			deploy.rindex("'$REMOTE_APPLY_RELEASE_PATH' '$remote_archive'"),
			deploy.rindex("run-migrate.sh'"),
		)
		self.assertLess(deploy.rindex("run-migrate.sh'"), deploy.rindex("bench restart"))
		self.assertLess(
			rollback.rindex("'$REMOTE_APPLY_RELEASE_PATH' '$remote_archive'"),
			rollback.rindex("bench restart"),
		)
		self.assertLess(
			rollback.rindex("bench restart"),
			rollback.rindex("assert_operational_health_ready"),
		)
		self.assertIn("readlink -f", smoke)
		self.assertIn("$REMOTE_RELEASE_ROOT/$RELEASE_COMMIT/source", smoke)
		self.assertNotIn('remote "cd', deploy)
		self.assertNotIn('remote "cd', rollback)
		self.assertNotIn('remote "cd', smoke)


if __name__ == "__main__":
	unittest.main()
