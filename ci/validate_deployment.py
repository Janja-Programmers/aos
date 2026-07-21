from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

REQUIRED_SECRET_REFERENCES = {
	"AOS_IMAGE_DIGESTS_JSON",
	"DEPLOY_HOST",
	"DEPLOY_USER",
	"DEPLOY_SSH_PRIVATE_KEY",
	"DEPLOY_KNOWN_HOSTS",
}
REQUIRED_VARIABLE_REFERENCES = {
	"REMOTE_BENCH_ROOT",
	"FRAPPE_SITE",
	"REMOTE_RELEASE_ROOT",
	"REMOTE_APPLY_RELEASE_PATH",
}


def _run(command: list[str], *, root: Path, env: dict[str, str]) -> str:
	result = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, check=False)
	if result.returncode != 0:
		raise RuntimeError(f"{' '.join(command)} failed:\n{result.stdout}\n{result.stderr}")
	return result.stdout + result.stderr


def _step_index(job: dict[str, Any], name: str) -> int:
	for index, step in enumerate(job.get("steps") or []):
		if str(step.get("name") or "") == name:
			return index
	return -1


def _validate_job_order(jobs: dict[str, Any], errors: list[str]) -> None:
	expectations = {
		"staging": ("Staging preflight", "Deploy staging", "Staging smoke checks"),
		"production": (
			"Production preflight and migration gates",
			"Deploy production",
			"Production health and job checks",
		),
	}
	for job_name, names in expectations.items():
		job = jobs.get(job_name) or {}
		indexes = [_step_index(job, name) for name in names]
		if any(index < 0 for index in indexes):
			errors.append(f"{job_name} is missing required ordered steps: {names}")
		elif indexes != sorted(indexes) or len(set(indexes)) != len(indexes):
			errors.append(f"{job_name} must run preflight, deployment/migration, then smoke checks")


def _validate_deploy_script(root: Path, errors: list[str]) -> None:
	path = root / "scripts/deploy/deploy.sh"
	text = path.read_text(encoding="utf-8")
	if "scripts/deploy/run-migrate.sh" not in text:
		errors.append("repository deploy path does not invoke guarded run-migrate.sh")
	if "REMOTE_DEPLOY_COMMAND" in text:
		errors.append("deployment still accepts arbitrary REMOTE_DEPLOY_COMMAND contents")
	if "REMOTE_APPLY_RELEASE_PATH" not in text:
		errors.append("deployment lacks the restricted release-apply executable path")
	apply_position = text.find("REMOTE_APPLY_RELEASE_PATH")
	migrate_position = text.rfind("scripts/deploy/run-migrate.sh")
	if apply_position < 0 or migrate_position < 0 or migrate_position <= apply_position:
		errors.append("guarded migration must run after the immutable release apply step")
	if not re.search(r"remote\s+\"[^\n]*run-migrate\.sh", text):
		errors.append("guarded migration is not invoked through the repository-controlled remote path")
	if re.search(r"if\s+.*(?:MIGRAT|SKIP).*;?\s*then[\s\S]{0,500}run-migrate\.sh", text, re.I):
		errors.append("migration invocation is conditionally skippable")
	if "set -Eeuo pipefail" not in text:
		errors.append("deployment script must stop immediately on migration failure")


def _write_fake_bench(path: Path, exit_code: int) -> None:
	path.write_text(
		f"#!/usr/bin/env bash\nset -Eeuo pipefail\nprintf 'fake bench invoked\\n'\nexit {exit_code}\n",
		encoding="utf-8",
	)
	path.chmod(0o755)


def _validate_migration_wrapper(root: Path, base_env: dict[str, str], errors: list[str]) -> None:
	with tempfile.TemporaryDirectory() as directory:
		temp = Path(directory)
		bench_root = temp / "bench"
		bin_dir = temp / "bin"
		bench_root.mkdir()
		bin_dir.mkdir()
		fake_bench = bin_dir / "bench"
		marker = temp / "state" / "last-migration-failed.env"
		env = {
			**base_env,
			"PATH": f"{bin_dir}:{base_env.get('PATH', '')}",
			"REMOTE_BENCH_ROOT": str(bench_root),
			"FRAPPE_SITE": "site.invalid",
			"RELEASE_COMMIT": "0123456789abcdef0123456789abcdef01234567",
			"AOS_MIGRATION_FAILURE_MARKER": str(marker),
		}

		_write_fake_bench(fake_bench, 23)
		failed = subprocess.run(
			["bash", "scripts/deploy/run-migrate.sh"],
			cwd=root,
			env=env,
			text=True,
			capture_output=True,
			check=False,
		)
		if failed.returncode == 0:
			errors.append("guarded migration wrapper did not fail when bench migrate failed")
		if not marker.is_file():
			errors.append("guarded migration wrapper did not atomically record failure")
		else:
			mode = stat.S_IMODE(marker.stat().st_mode)
			if mode != 0o600:
				errors.append(f"migration failure marker mode is {oct(mode)}, expected 0o600")
			content = marker.read_text(encoding="utf-8")
			required = {
				"MIGRATION_FAILURE_VERSION=1",
				"FRAPPE_SITE=site.invalid",
				"RELEASE_COMMIT=0123456789abcdef0123456789abcdef01234567",
				"ERROR_CATEGORY=MIGRATION_COMMAND_FAILED",
			}
			for item in required:
				if item not in content:
					errors.append(f"migration failure marker is missing: {item}")
			for forbidden in ("traceback", "password", "token=", "private key", "/srv/private"):
				if forbidden.lower() in content.lower():
					errors.append(f"migration failure marker leaked forbidden detail: {forbidden}")

		marker.parent.mkdir(parents=True, exist_ok=True)
		marker.write_text("stale failure\n", encoding="utf-8")
		marker.chmod(0o600)
		_write_fake_bench(fake_bench, 0)
		succeeded = subprocess.run(
			["bash", "scripts/deploy/run-migrate.sh"],
			cwd=root,
			env=env,
			text=True,
			capture_output=True,
			check=False,
		)
		if succeeded.returncode != 0:
			errors.append(
				"guarded migration wrapper failed in success simulation: "
				f"{succeeded.stdout}\n{succeeded.stderr}"
			)
		if marker.exists():
			errors.append("successful guarded migration did not clear the previous failure marker")


def main() -> int:
	root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
	workflow = root / ".github" / "workflows" / "deploy.yml"
	errors: list[str] = []
	text = workflow.read_text(encoding="utf-8")
	data = yaml.load(text, Loader=yaml.BaseLoader)
	jobs = data.get("jobs") or {}

	if set(jobs) != {"release", "staging", "production"}:
		errors.append("deployment workflow must contain release, staging, and production jobs")
	if jobs.get("staging", {}).get("environment") != "staging":
		errors.append("staging must use the staging GitHub Environment")
	if jobs.get("production", {}).get("environment") != "production":
		errors.append("production must use the protected production GitHub Environment")
	if "staging" not in set(jobs.get("production", {}).get("needs") or []):
		errors.append("production must depend on staging")
	if jobs.get("production", {}).get("if") != "github.event_name == 'workflow_run'":
		errors.append("manual workflow dispatch must not deploy production")
	if "CI" not in ((data.get("on") or {}).get("workflow_run") or {}).get("workflows", []):
		errors.append("deployment must be triggered only after the named CI workflow")
	_validate_job_order(jobs, errors)

	secret_refs = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
	variable_refs = set(re.findall(r"vars\.([A-Z0-9_]+)", text))
	missing_secrets = REQUIRED_SECRET_REFERENCES - secret_refs
	missing_vars = REQUIRED_VARIABLE_REFERENCES - variable_refs
	if missing_secrets:
		errors.append(f"missing protected secret declarations: {sorted(missing_secrets)}")
	if missing_vars:
		errors.append(f"missing protected variable declarations: {sorted(missing_vars)}")

	prohibited = (
		"BEGIN OPENSSH PRIVATE KEY",
		"hooks.slack.com/services/",
		"discord.com/api/webhooks/",
		"password=",
		"ssh-rsa ",
	)
	for token in prohibited:
		if token.lower() in text.lower():
			errors.append(f"deployment workflow contains prohibited credential material: {token}")

	_validate_deploy_script(root, errors)
	scripts = [
		root / "scripts" / "deploy" / name
		for name in ("lib.sh", "preflight.sh", "deploy.sh", "smoke.sh", "rollback.sh", "run-migrate.sh")
	]
	for script in scripts:
		if not script.is_file() or not os.access(script, os.X_OK):
			errors.append(f"deployment script missing or not executable: {script.relative_to(root)}")

	if not errors:
		try:
			with tempfile.TemporaryDirectory() as directory:
				temp = Path(directory)
				artifact = temp / "aos-release.tar.gz"
				manifest = temp / "release-manifest.json"
				artifact.write_bytes(b"immutable-release-test")
				commit = "0123456789abcdef0123456789abcdef01234567"
				base_env = {
					**os.environ,
					"DEPLOY_ENVIRONMENT": "staging",
					"RELEASE_COMMIT": commit,
					"RELEASE_ARTIFACT": str(artifact),
					"RELEASE_MANIFEST": str(manifest),
					"CI_GATE_VERIFIED": "true",
					"REMOTE_BENCH_ROOT": "/srv/aos-bench",
					"FRAPPE_SITE": "site.invalid",
					"REMOTE_RELEASE_ROOT": "/srv/aos-releases",
					"REMOTE_APPLY_RELEASE_PATH": "/usr/local/sbin/aos-apply-release",
				}
				_run(
					[
						sys.executable,
						"scripts/deploy/release_manifest.py",
						"create",
						str(manifest),
						str(artifact),
						commit,
					],
					root=root,
					env=base_env,
				)
				manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
				if not manifest_data.get("container_image_digests"):
					errors.append("release manifest did not record immutable container image digests")
				_run(["bash", "scripts/deploy/preflight.sh", "--dry-run"], root=root, env=base_env)
				deploy_output = _run(
					["bash", "scripts/deploy/deploy.sh", "--dry-run"], root=root, env=base_env
				)
				if "unconditionally run scripts/deploy/run-migrate.sh" not in deploy_output:
					errors.append("deployment dry run does not explicitly confirm guarded migration")
				_run(["bash", "scripts/deploy/smoke.sh", "--dry-run"], root=root, env=base_env)
				_run(["bash", "scripts/deploy/run-migrate.sh", "--dry-run"], root=root, env=base_env)
				rollback_env = {
					**base_env,
					"ROLLBACK_COMMIT": commit,
					"ROLLBACK_MANIFEST": str(manifest),
					"VERIFIED_BACKUP_ID": "20260719T120000Z",
				}
				_run(["bash", "scripts/deploy/rollback.sh", "--dry-run"], root=root, env=rollback_env)
				_validate_migration_wrapper(root, base_env, errors)
		except Exception as exc:
			errors.append(str(exc))

	if errors:
		print("Controlled deployment validation failed:", file=sys.stderr)
		for error in errors:
			print(f"- {error}", file=sys.stderr)
		return 1
	print(
		"Controlled deployment order, guarded migration failure/success behavior, immutable manifest, "
		"protected environments, and dry runs: OK"
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
