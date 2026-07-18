from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

SHA_USES = re.compile(r"^\s*-?\s*uses:\s*[^\s]+@([0-9a-f]{40})\s*(?:#\s*v?\d[^\s]*)?\s*$")
USES = re.compile(r"^\s*-?\s*uses:\s*(.+)$")
APPROVED_PERMISSIONS = {"contents": "read"}


def validate_permissions(path: Path, job_id: str, permissions, errors: list[str]) -> None:
	"""Reject job overrides that can broaden the read-only token."""
	if permissions is None:
		return
	if not isinstance(permissions, dict):
		errors.append(f"{path}: job {job_id} permissions must be an empty map or contents: read")
		return
	if permissions not in ({}, APPROVED_PERMISSIONS):
		details = ", ".join(f"{scope}={level}" for scope, level in sorted(permissions.items()))
		errors.append(f"{path}: job {job_id} has unapproved permission override ({details})")


def validate_required_gate(path: Path, jobs: dict, errors: list[str]) -> None:
	gate = jobs.get("required-gate")
	if not gate:
		errors.append(f"{path}: required-gate job is missing")
		return
	if gate.get("name") != "CI / Required Gate":
		errors.append(f"{path}: required-gate must expose the stable name 'CI / Required Gate'")
	expected = set(jobs) - {"required-gate"}
	actual = set(gate.get("needs", []))
	if actual != expected:
		errors.append(
			f"{path}: required-gate needs mismatch; missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
		)
	if gate.get("if") != "always()":
		errors.append(f"{path}: required-gate must use if: always()")
	runs = "\n".join(str(step.get("run", "")) for step in gate.get("steps", []) if isinstance(step, dict))
	if 'result != "success"' not in runs:
		errors.append(f"{path}: required-gate does not fail closed on every non-success result")


def main() -> int:
	root = Path(__file__).resolve().parents[1]
	workflow_dir = root / ".github" / "workflows"
	errors: list[str] = []
	workflows = sorted([*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")])
	if not workflows:
		errors.append("no workflow files found")

	for path in workflows:
		text = path.read_text(encoding="utf-8")
		for line_number, line in enumerate(text.splitlines(), start=1):
			match = USES.match(line)
			if match and not SHA_USES.match(line):
				errors.append(
					f"{path.relative_to(root)}:{line_number}: action is not a full SHA with version comment"
				)
		data = yaml.load(text, Loader=yaml.BaseLoader)
		if data.get("permissions") != APPROVED_PERMISSIONS:
			errors.append(f"{path.relative_to(root)}: top-level permissions must be contents: read")
		if "pull_request_target" in data.get("on", {}):
			errors.append(f"{path.relative_to(root)}: pull_request_target is prohibited")
		jobs = data.get("jobs", {})
		for job_id, job in jobs.items():
			validate_permissions(path.relative_to(root), job_id, job.get("permissions"), errors)
			if "timeout-minutes" not in job:
				errors.append(f"{path.relative_to(root)}: job {job_id} has no timeout-minutes")
			if job.get("runs-on") != "ubuntu-24.04":
				errors.append(f"{path.relative_to(root)}: job {job_id} must use ubuntu-24.04")
			if "continue-on-error" in job:
				errors.append(f"{path.relative_to(root)}: job {job_id} uses prohibited continue-on-error")
			if "environment" in job:
				errors.append(
					f"{path.relative_to(root)}: job {job_id} must not target a deployment environment"
				)
			for step in job.get("steps", []):
				if isinstance(step, dict) and "continue-on-error" in step:
					errors.append(
						f"{path.relative_to(root)}: job {job_id} step uses prohibited continue-on-error"
					)
		validate_required_gate(path.relative_to(root), jobs, errors)

	if errors:
		print("GitHub Actions policy failed:", file=sys.stderr)
		for error in errors:
			print(f"- {error}", file=sys.stderr)
		return 1
	print("GitHub Actions pinning, permissions, runner, and timeout policy: OK")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
