"""Fetch a reviewed GHCR promotion receipt from the exact successful publish run."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "deploy"))
from image_lock import verify as verify_lock

_RELEASE = re.compile(r"^[0-9a-f]{40}$")
_RUN = re.compile(r"^[1-9][0-9]{0,17}$")
_REPOSITORY = "Janja-Programmers/aos"


def _api(path: str) -> dict[str, Any]:
	result = subprocess.run(
		["gh", "api", "-X", "GET", f"repos/{_REPOSITORY}/{path}"],
		check=True,
		text=True,
		capture_output=True,
		timeout=60,
	)
	value = json.loads(result.stdout)
	if not isinstance(value, dict):
		raise ValueError("GitHub returned invalid promotion metadata.")
	return value


def verify_promotion(commit: str, run_id: str) -> str:
	if not _RELEASE.fullmatch(commit) or not _RUN.fullmatch(run_id):
		raise ValueError("Promotion requires an exact release SHA and numeric run ID.")
	if os.getenv("GITHUB_REPOSITORY") != _REPOSITORY or not os.getenv("GH_TOKEN"):
		raise ValueError("Promotion must be checked with a read-scoped canonical repository token.")
	run = _api(f"actions/runs/{run_id}")
	source = run.get("head_repository") or {}
	if any(
		(
			run.get("id") != int(run_id),
			run.get("name") != "Manual OCI image promotion",
			not re.fullmatch(
				r"(?:[^/]+/[^/]+/)?\.github/workflows/image-promotion\.yml(?:@main)?",
				str(run.get("path") or ""),
			),
			run.get("event") != "workflow_dispatch",
			run.get("head_sha") != commit,
			run.get("head_branch") != "main",
			source.get("full_name") != _REPOSITORY,
			run.get("status") != "completed",
			run.get("conclusion") != "success",
		)
	):
		raise ValueError("Requested image-promotion run is not an approved successful release publication.")
	jobs = _api(f"actions/runs/{run_id}/jobs?per_page=100&filter=latest")
	published = [
		job
		for job in jobs.get("jobs") or []
		if job.get("name") == "publish"
		and job.get("conclusion") == "success"
		and job.get("run_attempt") == run.get("run_attempt")
	]
	if len(published) != 1:
		raise ValueError("Promotion job did not successfully publish on the selected attempt.")
	name = f"promoted-images-{commit}-{run_id}"
	items = _api(f"actions/runs/{run_id}/artifacts?per_page=100").get("artifacts") or []
	matching = [item for item in items if item.get("name") == name and not item.get("expired")]
	if len(matching) != 1:
		raise ValueError("The exact, unexpired promoted OCI image artifact is missing or ambiguous.")
	return name


def fetch(commit: str, run_id: str, manifest_path: Path, output: Path) -> None:
	name = verify_promotion(commit, run_id)
	with tempfile.TemporaryDirectory(prefix="aos-promotion-evidence-") as directory:
		work = Path(directory)
		subprocess.run(
			[
				"gh",
				"run",
				"download",
				run_id,
				"--repo",
				_REPOSITORY,
				"--name",
				name,
				"--dir",
				str(work),
			],
			check=True,
			capture_output=True,
			text=True,
			timeout=120,
		)
		packaged_manifest = work / "release-manifest.json"
		lock_path = work / "promoted-images" / "release-image-lock.json"
		if not packaged_manifest.is_file() or not lock_path.is_file():
			raise ValueError("Promotion evidence has missing or unexpected archive layout.")
		expected_hash = hashlib.sha256(manifest_path.read_bytes()).digest()
		if hashlib.sha256(packaged_manifest.read_bytes()).digest() != expected_hash:
			raise ValueError("Promoted release manifest bytes do not match this exact deployment release.")
		verify_lock(manifest_path, lock_path, commit, registry=False)
		if output.exists():
			raise ValueError("A prior promotion receipt must not be silently replaced.")
		# Exclusive creation prevents concurrent runs from replacing a receipt.
		with lock_path.open("rb") as source:
			with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as destination:
				shutil.copyfileobj(source, destination)


def main() -> None:
	if len(sys.argv) != 5:
		raise SystemExit("usage: fetch_promoted_lock.py SHA PROMOTION_RUN_ID RELEASE_MANIFEST LOCK_OUTPUT")
	fetch(sys.argv[1], sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))


if __name__ == "__main__":
	main()
