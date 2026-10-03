from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

SHA_USES = re.compile(r"^\s*-?\s*uses:\s*[^\s]+@([0-9a-f]{40})\s*(?:#\s*v?\d[^\s]*)?\s*$")
USES = re.compile(r"^\s*-?\s*uses:\s*(.+)$")
APPROVED_PERMISSIONS = {"contents": "read"}


def validate_permissions(path: Path, job_id: str, permissions, errors: list[str]) -> None:
	"""Allow the narrowly scoped manual publisher to write GHCR; all other jobs stay read-only."""
	if path.name == "image-promotion.yml":
		expected = {
			"plan": {"contents": "read", "actions": "read"},
			"publish": {"contents": "read", "actions": "read", "packages": "write"},
		}.get(job_id)
		if permissions != expected:
			errors.append(f"{path}: job {job_id} has invalid manual image-promotion permissions")
		return
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


def validate_promotion_workflow(path: Path, data: dict, jobs: dict, errors: list[str]) -> None:
	"""Image publication must be an explicit reviewed main-only action, never a CI or PR side effect."""
	if set(data.get("on") or {}) != {"workflow_dispatch"}:
		errors.append(f"{path}: image promotion must be manual workflow_dispatch only")
	inputs = ((data.get("on") or {}).get("workflow_dispatch") or {}).get("inputs") or {}
	if set(inputs) != {"release_sha", "publish"}:
		errors.append(f"{path}: image promotion inputs must be exact SHA and explicit publish flag")
	flag = inputs.get("publish") or {}
	if flag.get("type") != "boolean" or flag.get("default") != "false":
		errors.append(f"{path}: GHCR publication must be disabled by default")
	if set(jobs) != {"plan", "publish"}:
		errors.append(f"{path}: image promotion must contain only read-only plan and protected publish jobs")
	plan = jobs.get("plan") or {}
	publish = jobs.get("publish") or {}
	if plan.get("if") != "github.ref == 'refs/heads/main' && github.repository == 'Janja-Programmers/aos'":
		errors.append(f"{path}: image-promotion plan must be restricted to canonical main")
	if plan.get("environment"):
		errors.append(f"{path}: read-only promotion plan must not request a deployment environment")
	if publish.get("if") != "inputs.publish == true && needs.plan.result == 'success'":
		errors.append(f"{path}: GHCR publication must require explicit input and successful plan")
	if publish.get("needs") != "plan" or publish.get("environment") != "image-promotion":
		errors.append(f"{path}: GHCR publication requires the approved plan and protected Environment")
	if (data.get("concurrency") or {}).get("cancel-in-progress") != "false":
		errors.append(f"{path}: promotion runs must never be cancelled during registry publication")
	plan_runs = "\\n".join(str(step.get("run") or "") for step in plan.get("steps") or [])
	publish_runs = "\\n".join(str(step.get("run") or "") for step in publish.get("steps") or [])
	for required in (
		"verify-promotion-source.sh",
		"--release-run",
		"release_manifest.py verify",
		"promotion_plan.py create",
		"promotion_plan.py verify",
	):
		if required not in plan_runs:
			errors.append(f"{path}: promotion plan does not verify exact CI-green release: {required}")
	for required in (
		"verify-promotion-source.sh",
		'AOS_IMAGE_PROMOTION_ENABLED" == "true"',
		"promotion_plan.py verify",
		"publish_images.py publish",
	):
		if required not in publish_runs:
			errors.append(f"{path}: GHCR publication lacks required fail-closed guard: {required}")
	guard = next(
		(step for step in publish.get("steps") or [] if step.get("name") == "Reverify freshness, CI and protected enablement"),
		{},
	)
	if (guard.get("env") or {}).get("AOS_IMAGE_PROMOTION_ENABLED") != "${{ vars.AOS_IMAGE_PROMOTION_ENABLED }}":
		errors.append(f"{path}: publish enablement must be sourced from protected Environment")
	if not any("docker/setup-buildx-action@" in str(step.get("uses") or "") for step in publish.get("steps") or []):
		errors.append(f"{path}: promotion requires an immutable Buildx action pin")


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
		is_deployment_workflow = path.name in {"deploy.yml", "deploy.yaml"}
		is_promotion_workflow = path.name == "image-promotion.yml"
		for job_id, job in jobs.items():
			validate_permissions(path.relative_to(root), job_id, job.get("permissions"), errors)
			if "timeout-minutes" not in job:
				errors.append(f"{path.relative_to(root)}: job {job_id} has no timeout-minutes")
			if job.get("runs-on") != "ubuntu-24.04":
				errors.append(f"{path.relative_to(root)}: job {job_id} must use ubuntu-24.04")
			if "continue-on-error" in job:
				errors.append(f"{path.relative_to(root)}: job {job_id} uses prohibited continue-on-error")
			if "environment" in job and not is_deployment_workflow and not (
				is_promotion_workflow and job_id == "publish"
			):
				errors.append(
					f"{path.relative_to(root)}: job {job_id} must not target a deployment environment"
				)
			for step in job.get("steps", []):
				if isinstance(step, dict) and "continue-on-error" in step:
					errors.append(
						f"{path.relative_to(root)}: job {job_id} step uses prohibited continue-on-error"
					)
				if isinstance(step, dict):
					script = str(step.get("run") or "")
					for pipe in re.finditer(r"\|\s*tee\b", script):
						guard = re.search(r"(?m)^\s*set -Eeuo pipefail\s*$", script[: pipe.start()])
						if not guard:
							errors.append(
								f"{path.relative_to(root)}: job {job_id} pipes execution through tee without prior pipefail"
							)
		if is_deployment_workflow:
			if set(jobs) != {"release", "staging", "production"}:
				errors.append(
					f"{path.relative_to(root)}: deployment jobs must be release, staging, production"
				)
			if jobs.get("staging", {}).get("environment") != "staging":
				errors.append(f"{path.relative_to(root)}: staging job must target the staging environment")
			if jobs.get("production", {}).get("environment") != "production":
				errors.append(
					f"{path.relative_to(root)}: production job must target the protected production environment"
				)
			production_needs = set(jobs.get("production", {}).get("needs", []))
			if not {"release", "staging"}.issubset(production_needs):
				errors.append(f"{path.relative_to(root)}: production must require release and staging")
			workflow_run = data.get("on", {}).get("workflow_run", {})
			if "CI" not in workflow_run.get("workflows", []):
				errors.append(f"{path.relative_to(root)}: deployment must be gated by the CI workflow")
			if jobs.get("production", {}).get("if") != "github.event_name == 'workflow_run'":
				errors.append(f"{path.relative_to(root)}: manual dispatch must never reach production")
		elif is_promotion_workflow:
			validate_promotion_workflow(path.relative_to(root), data, jobs, errors)
		else:
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
