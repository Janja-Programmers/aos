from __future__ import annotations

import hashlib
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
		"staging": ("Refuse stale staging release", "Staging preflight", "Deploy staging", "Staging smoke checks"),
		"production": (
			"Refuse stale production release",
			"Enforce production deployment enablement",
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
			errors.append(f"{job_name} must verify freshness, then run preflight, deployment/migration, then smoke checks")


def _validate_production_authorization(job: dict[str, Any], errors: list[str]) -> None:
	steps = job.get("steps") or []
	guard = next((step for step in steps if step.get("name") == "Enforce production deployment enablement"), {})
	if not guard:
		errors.append("production must have an explicit enablement check")
		return
	if guard.get("if"):
		errors.append("production enablement guard must not be conditionally skippable")
	if (guard.get("env") or {}).get("PRODUCTION_DEPLOYMENT_ENABLED") != "${{ vars.AOS_PRODUCTION_DEPLOYMENT_ENABLED }}":
		errors.append("production enablement must come from the protected production Environment variable")
	script = str(guard.get("run") or "")
	if not script or "== \"true\"" not in script:
		errors.append("production enablement must require the exact value true")
		return
	for supplied, expected_success in (("", False), ("false", False), ("1", False), ("TRUE", False), ("true", True)):
		result = subprocess.run(
			["bash", "-Eeuo", "pipefail", "-c", script],
			env={**os.environ, "PRODUCTION_DEPLOYMENT_ENABLED": supplied},
			capture_output=True,
			text=True,
			check=False,
		)
		if (result.returncode == 0) != expected_success:
			errors.append(f"production enablement unexpectedly handled {supplied!r}: exit {result.returncode}")


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


def _validate_rollback_script(root: Path, errors: list[str]) -> None:
	text = (root / "scripts/deploy/rollback.sh").read_text(encoding="utf-8")
	if "REMOTE_ROLLBACK_COMMAND" in text:
		errors.append("rollback must not accept arbitrary remote command text")
	for required in (
		"release_manifest.py verify ",
		"ROLLBACK_ARTIFACT",
		"REMOTE_APPLY_RELEASE_PATH",
		"REMOTE_RELEASE_ROOT",
		"ROLLBACK_APPROVED",
		"ROLLBACK_DB_DECISION",
		"application-only",
		"sha256sum -c -",
		"assert_operational_health_ready",
		"assert_job_monitoring_ready",
	):
		if required not in text:
			errors.append(f"rollback integrity, approval or post-apply check missing: {required}")


def _assert_rejected(
	command: list[str], *, root: Path, env: dict[str, str], reason: str, errors: list[str]
) -> None:
	result = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, check=False)
	if result.returncode == 0 or reason not in result.stderr + result.stdout:
		errors.append(
			f"rollback did not reject {reason!r} as required (exit={result.returncode}): "
			f"{result.stdout}{result.stderr}"
		)


def _validate_release_inventory(
	root: Path,
	manifest: Path,
	artifact: Path,
	commit: str,
	data: dict[str, Any],
	env: dict[str, str],
	errors: list[str],
) -> None:
	compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
	services = compose["services"]
	expected_builds = {name for name, service in services.items() if service.get("build")}
	expected_external = {
		service["image"].split("@", 1)[0]
		for service in services.values()
		if service.get("image") and "@sha256:" in service["image"]
	}
	expected_variables = {
		name: re.fullmatch(r"\$\{([A-Z][A-Z0-9_]*):\?[^}]+\}", service["image"]).group(1)
		for name, service in services.items()
		if service.get("image") and service["image"].startswith("${")
	}
	if data.get("schema_version") != 2:
		errors.append("release provenance requires manifest schema 2")
	if set(data.get("source_build_contexts") or {}) != expected_builds:
		errors.append("release manifest omitted or invented source-built Compose services")
	if set(data.get("container_image_digests") or {}) != expected_external:
		errors.append("release manifest omitted or invented static external Compose images")
	if data.get("runtime_image_variables") != expected_variables:
		errors.append("release manifest omitted or invented runtime image-variable requirements")
	for service, item in (data.get("source_build_contexts") or {}).items():
		if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("source_sha256") or "")):
			errors.append(f"{service}: missing archive source fingerprint")
	_run(
		[sys.executable, "scripts/deploy/release_manifest.py", "verify", str(manifest), str(artifact), commit],
		root=root,
		env=env,
	)
	# Manifest claims must match the release archive itself, not just pass schema checks.
	original = manifest.read_text(encoding="utf-8")
	builds = data.get("source_build_contexts") or {}
	if builds:
		tampered = json.loads(original)
		first = next(iter(builds))
		tampered["source_build_contexts"][first]["source_sha256"] = "0" * 64
		manifest.write_text(json.dumps(tampered), encoding="utf-8")
		try:
			result = subprocess.run(
				[sys.executable, "scripts/deploy/release_manifest.py", "verify", str(manifest), str(artifact), commit],
				cwd=root,
				env=env,
				text=True,
				capture_output=True,
				check=False,
			)
			if result.returncode == 0 or "Release source-build inventory mismatch" not in result.stderr + result.stdout:
				errors.append("release manifest accepted fabricated build-context provenance")
		finally:
			manifest.write_text(original, encoding="utf-8")


def _validate_image_lock(
    root: Path,
    manifest: Path,
    commit: str,
    data: dict[str, Any],
    env: dict[str, str],
    temp: Path,
    errors: list[str],
) -> None:
    contexts = {entry["context"] for entry in data["source_build_contexts"].values()}
    builds = {
        context: (
            "ghcr.io/janja-programmers/aos-"
            + context.removeprefix("infra/").replace("/", "-")
            + "@sha256:"
            + hashlib.sha256(("build:" + context).encode()).hexdigest()
        )
        for context in contexts
    }
    runtime = {
        service: "ghcr.io/valhalla/valhalla-scripted@sha256:"
        + hashlib.sha256(("runtime:" + service).encode()).hexdigest()
        for service in data["runtime_image_variables"]
    }
    build_receipts = temp / "image-build-receipts.json"
    runtime_receipts = temp / "image-runtime-receipts.json"
    lock = temp / "release-image-lock.json"
    build_receipts.write_text(json.dumps(builds), encoding="utf-8")
    runtime_receipts.write_text(json.dumps(runtime), encoding="utf-8")
    command = [sys.executable, "scripts/deploy/image_lock.py"]
    _run(
        [*command, "create", str(manifest), str(build_receipts), str(runtime_receipts), str(lock), commit],
        root=root,
        env=env,
    )
    _run([*command, "verify", str(manifest), str(lock), commit], root=root, env=env)

    original = json.loads(lock.read_text(encoding="utf-8"))
    first = sorted(contexts)[0]
    test_cases = (
        ("missing-context", "Image lock is missing or inventing a source-build context."),
        ("false-source", "image receipt source fingerprint mismatch."),
        ("false-image", "Image must have a real-looking immutable OCI sha256 reference."),
        ("wrong-manifest", "Image lock does not match this exact release manifest."),
    )
    for scenario, reason in test_cases:
        altered = json.loads(json.dumps(original))
        if scenario == "missing-context":
            del altered["build_context_images"][first]
        elif scenario == "false-source":
            altered["build_context_images"][first]["source_sha256"] = "0" * 64
        elif scenario == "false-image":
            altered["build_context_images"][first]["image_ref"] = "ghcr.io/janja-programmers/aos:latest"
        else:
            altered["release_manifest_sha256"] = "0" * 64
        lock.write_text(json.dumps(altered), encoding="utf-8")
        _assert_rejected(
            [*command, "verify", str(manifest), str(lock), commit],
            root=root,
            env=env,
            reason=reason,
            errors=errors,
        )
    lock.write_text(json.dumps(original), encoding="utf-8")


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

	concurrency = data.get("concurrency") or {}
	if concurrency.get("group") != "aos-controlled-deployment-main":
		errors.append("all deployment runs must share one global concurrency group")
	if concurrency.get("cancel-in-progress") != "false":
		errors.append("an active deployment must never be cancelled by a newer release")
	release_job = jobs.get("release") or {}
	release_if = str(release_job.get("if") or "")
	for required in (
		"github.event.workflow_run.conclusion == 'success'",
		"github.event.workflow_run.event == 'push'",
		"github.event.workflow_run.head_branch == 'main'",
		"github.event.workflow_run.head_repository.full_name == github.repository",
	):
		if required not in release_if:
			errors.append(f"release eligibility is missing: {required}")
	release_steps = release_job.get("steps") or []
	release_guard = next(
		(str(step.get("run") or "") for step in release_steps if step.get("name") == "Verify eligible trigger and current release"),
		"",
	)
	for required in ("MANUAL_DRY_RUN", "SOURCE_EVENT", "SOURCE_BRANCH", "SOURCE_REPOSITORY", "git ls-remote", "RELEASE_COMMIT"):
		if required not in release_guard:
			errors.append(f"release trigger and freshness validation is missing: {required}")
	for environment in ("staging", "production"):
		guard_name = f"Refuse stale {environment} release"
		steps = (jobs.get(environment) or {}).get("steps") or []
		guard = next((step for step in steps if step.get("name") == guard_name), {})
		if "git ls-remote" not in str(guard.get("run") or ""):
			errors.append(f"{environment} lacks an independent latest-main freshness check")
		if guard.get("if") != "github.event_name == 'workflow_run'":
			errors.append(f"{environment} freshness check must apply to automatic CI-triggered deployments")

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
	_validate_production_authorization(jobs.get("production") or {}, errors)

	if "AOS_IMAGE_DIGESTS_JSON" in text:
		errors.append("release must derive image inventory from immutable source, not arbitrary digest overrides")
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
	_validate_rollback_script(root, errors)
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
				commit = _run(["git", "rev-parse", "HEAD"], root=root, env=os.environ.copy()).strip()
				_run(
					["git", "archive", "--format=tar.gz", f"--output={artifact}", "HEAD"],
					root=root,
					env=os.environ.copy(),
				)
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
				_validate_release_inventory(root, manifest, artifact, commit, manifest_data, base_env, errors)
				_validate_image_lock(root, manifest, commit, manifest_data, base_env, temp, errors)
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
					"ROLLBACK_ARTIFACT": str(artifact),
					"VERIFIED_BACKUP_ID": "20260719T120000Z",
					"ROLLBACK_APPROVED": "false",
					"ROLLBACK_DB_DECISION": "",
				}
				_run(["bash", "scripts/deploy/rollback.sh", "--dry-run"], root=root, env=rollback_env)
				_assert_rejected(
					["bash", "scripts/deploy/rollback.sh", "--dry-run"],
					root=root,
					env={**rollback_env, "VERIFIED_BACKUP_ID": "invalid;exit 0"},
					reason="VERIFIED_BACKUP_ID is invalid",
					errors=errors,
				)
				_assert_rejected(
					["bash", "scripts/deploy/rollback.sh"],
					root=root,
					env=rollback_env,
					reason="ROLLBACK_APPROVED=true is required",
					errors=errors,
				)
				_assert_rejected(
					["bash", "scripts/deploy/rollback.sh"],
					root=root,
					env={**rollback_env, "ROLLBACK_APPROVED": "true"},
					reason="ROLLBACK_DB_DECISION=application-only is required",
					errors=errors,
				)
				artifact.write_bytes(b"tampered-archive")
				_assert_rejected(
					["bash", "scripts/deploy/rollback.sh", "--dry-run"],
					root=root,
					env=rollback_env,
					reason="Release artifact checksum mismatch",
					errors=errors,
				)
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
