from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import yaml

DIRECT = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s;]+)$")
LOCKED = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)(?:\s+\\)?$")
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST_IMAGE = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def normalize(name: str) -> str:
	return re.sub(r"[-_.]+", "-", name).lower()


def direct_requirements(lines: list[str], source: str, failures: list[str]) -> dict[str, str]:
	requirements: dict[str, str] = {}
	for line_number, raw in enumerate(lines, 1):
		line = raw.strip()
		if not line or line.startswith("#"):
			continue
		match = DIRECT.fullmatch(line)
		if not match:
			failures.append(f"{source}:{line_number}: direct requirement is not exactly pinned")
			continue
		requirements[normalize(match.group(1))] = match.group(2)
	return requirements


def locked_requirements(lines: list[str]) -> dict[str, str]:
	resolved: dict[str, str] = {}
	for raw in lines:
		match = LOCKED.fullmatch(raw.strip())
		if match:
			resolved[normalize(match.group(1))] = match.group(2)
	return resolved


def assert_lock(source: Path, lock: Path, failures: list[str], root: Path) -> None:
	if not source.is_file() or not lock.is_file():
		failures.append(f"missing source/lock pair: {source.relative_to(root)} -> {lock.relative_to(root)}")
		return
	expected = direct_requirements(
		source.read_text(encoding="utf-8").splitlines(), str(source.relative_to(root)), failures
	)
	resolved = locked_requirements(lock.read_text(encoding="utf-8").splitlines())
	for package, version in expected.items():
		if resolved.get(package) != version:
			failures.append(
				f"{lock.relative_to(root)}: expected direct pin {package}=={version}; regenerate locks"
			)
	if "--hash=sha256:" not in lock.read_text(encoding="utf-8"):
		failures.append(f"{lock.relative_to(root)}: no package hashes found")


def validate_versions(root: Path, failures: list[str]) -> None:
	values: dict[str, str] = {}
	for raw in (root / "ci" / "versions.env").read_text(encoding="utf-8").splitlines():
		line = raw.strip()
		if not line or line.startswith("#"):
			continue
		key, separator, value = line.partition("=")
		if not separator or not value:
			failures.append("ci/versions.env: malformed entry")
			continue
		values[key] = value

	for key in (
		"PYTHON_VERSION",
		"BACKGROUND_REMOVAL_PYTHON_VERSION",
		"FRAPPE_BENCH_VERSION",
		"NODE_VERSION",
		"SHELLCHECK_VERSION",
		"NGINX_VERSION",
		"PROMETHEUS_VERSION",
		"ALERTMANAGER_VERSION",
		"COMPOSE_VERSION",
		"CRANE_VERSION",
	):
		if not EXACT_VERSION.fullmatch(values.get(key, "")):
			failures.append(f"ci/versions.env: {key} must be an exact three-part version")
	if not values.get("PYTHON_VERSION", "").startswith("3.14."):
		failures.append("ci/versions.env: PYTHON_VERSION must be Python 3.14")
	if not values.get("BACKGROUND_REMOVAL_PYTHON_VERSION", "").startswith("3.13."):
		failures.append("ci/versions.env: BACKGROUND_REMOVAL_PYTHON_VERSION must be Python 3.13")
	for key in (
		"FRAPPE_REF",
		"FRAPPE_BENCH_REF",
		"NGINX_REF",
		"FRAPPE_SEMGREP_RULES_REF",
		"SEMGREP_COMMUNITY_RULES_REF",
	):
		if not SHA.fullmatch(values.get(key, "")):
			failures.append(f"ci/versions.env: {key} must be a full commit SHA")
	for key in (
		"SHELLCHECK_ARCHIVE_SHA256",
		"COMPOSE_BINARY_SHA256",
		"CRANE_ARCHIVE_SHA256",
		"PROMETHEUS_ARCHIVE_SHA256",
		"ALERTMANAGER_ARCHIVE_SHA256",
	):
		if not re.fullmatch(r"[0-9a-f]{64}", values.get(key, "")):
			failures.append(f"ci/versions.env: {key} must be a SHA-256")
	if not re.fullmatch(r"v\d+\.\d+\.\d+", values.get("FRAPPE_RELEASE", "")):
		failures.append("ci/versions.env: FRAPPE_RELEASE must be an exact stable release tag")
	for key in ("MARIADB_IMAGE", "REDIS_IMAGE", "PYTHON_IMAGE", "BACKGROUND_REMOVAL_PYTHON_IMAGE"):
		if not DIGEST_IMAGE.fullmatch(values.get(key, "")):
			failures.append(f"ci/versions.env: {key} must be an immutable digest reference")


def validate_matrices(root: Path, failures: list[str]) -> None:
	expected = (root / "ci" / "service-matrix.txt").read_text(encoding="utf-8").splitlines()
	standard_runtime = [service for service in expected if service != "background-removal"]
	workflow = yaml.safe_load((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
	for job_name in ("fastapi-unit-tests", "python314-runtime-compatibility"):
		actual = workflow["jobs"][job_name]["strategy"]["matrix"]["service"]
		if actual != standard_runtime:
			failures.append(
				f".github/workflows/ci.yml: {job_name} matrix must contain every standard Python 3.14 service and exclude background-removal"
			)
	if "background-removal-runtime" not in workflow.get("jobs", {}):
		failures.append(".github/workflows/ci.yml: dedicated background-removal-runtime job is missing")


def main() -> int:
	root = Path(__file__).resolve().parents[1]
	failures: list[str] = []
	for name in ("build", "quality", "security", "tests"):
		assert_lock(
			root / "ci" / "requirements" / f"{name}.in",
			root / "ci" / "requirements" / f"{name}.lock",
			failures,
			root,
		)
	for service in (root / "ci" / "service-matrix.txt").read_text(encoding="utf-8").splitlines():
		service_root = root / "infra" / service
		assert_lock(service_root / "requirements.txt", service_root / "requirements.lock", failures, root)
		assert_lock(
			service_root / "requirements-test.txt", service_root / "requirements-test.lock", failures, root
		)

	pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
	build_expected = direct_requirements(pyproject["build-system"]["requires"], "pyproject.toml", failures)
	build_source = direct_requirements(
		(root / "ci" / "requirements" / "build.in").read_text(encoding="utf-8").splitlines(),
		"ci/requirements/build.in",
		failures,
	)
	if build_expected != build_source:
		failures.append("ci/requirements/build.in differs from pyproject.toml build-system requirements")
	root_expected = direct_requirements(pyproject["project"]["dependencies"], "pyproject.toml", failures)
	root_lock = (root / "ci" / "requirements" / "root-production.lock").read_text(encoding="utf-8")
	root_resolved = locked_requirements(root_lock.splitlines())
	for package, version in root_expected.items():
		if root_resolved.get(package) != version:
			failures.append(f"ci/requirements/root-production.lock: missing {package}=={version}")

	validate_versions(root, failures)
	validate_matrices(root, failures)
	if failures:
		print("Foundation manifest/lock validation failed:", file=sys.stderr)
		for failure in failures:
			print(f"- {failure}", file=sys.stderr)
		return 1
	print("Version manifest, source/lock pairs, hashes, and service matrices: OK")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
