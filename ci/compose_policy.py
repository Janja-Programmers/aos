from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
PLACEHOLDER = re.compile(r"(?i)(replace[_-]?with|placeholder|example\.invalid|dummy|sentinel)")
PUBLIC_PORT_EXCEPTIONS = {"livekit"}


def fail(message: str, failures: list[str]) -> None:
	failures.append(message)


def validate_compose(path: Path) -> list[str]:
	data = yaml.safe_load(path.read_text(encoding="utf-8"))
	services = data.get("services", {})
	failures: list[str] = []
	if not services:
		return ["rendered Compose configuration defines no services"]

	for name, service in services.items():
		image = service.get("image")
		if image:
			if PLACEHOLDER.search(image):
				fail(f"{name}: placeholder/sentinel image reference is prohibited", failures)
			if ":latest" in image or image.endswith("/latest"):
				fail(f"{name}: mutable latest image is prohibited", failures)
			if not DIGEST.search(image):
				fail(f"{name}: image is not pinned by sha256 digest", failures)
			else:
				digest = image.rsplit("@sha256:", 1)[1]
				if len(set(digest)) < 8:
					fail(f"{name}: known sentinel-style digest is prohibited", failures)

		if not service.get("healthcheck"):
			fail(f"{name}: maintained service has no healthcheck", failures)

		for port in service.get("ports", []):
			if isinstance(port, str):
				host_ip = port.split(":", 1)[0] if port.count(":") >= 2 else ""
			else:
				host_ip = str(port.get("host_ip", ""))
			if name not in PUBLIC_PORT_EXCEPTIONS and host_ip != "127.0.0.1":
				fail(f"{name}: published port is not bound to 127.0.0.1", failures)

	return failures


def validate_dockerfiles(root: Path) -> list[str]:
	failures: list[str] = []
	for path in sorted((root / "infra").rglob("Dockerfile")):
		for line in path.read_text(encoding="utf-8").splitlines():
			if not line.startswith("FROM "):
				continue
			image = line.split()[1]
			if PLACEHOLDER.search(image):
				fail(f"{path.relative_to(root)}: placeholder base image is prohibited", failures)
			if image.lower() != "scratch" and not DIGEST.search(image):
				fail(f"{path.relative_to(root)}: base image is not digest-pinned", failures)
	return failures


def main() -> int:
	if len(sys.argv) != 2:
		raise SystemExit("usage: compose_policy.py RENDERED_COMPOSE_YAML")
	root = Path(__file__).resolve().parents[1]
	failures = validate_compose(Path(sys.argv[1])) + validate_dockerfiles(root)
	if failures:
		print("Compose policy validation failed:", file=sys.stderr)
		for message in failures:
			print(f"- {message}", file=sys.stderr)
		return 1
	print("Compose immutability, healthcheck, and host-binding policy: OK")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
