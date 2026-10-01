from __future__ import annotations

import sys
from pathlib import Path

import yaml


def fail(message: str, failures: list[str]) -> None:
	failures.append(message)


def main() -> int:
	root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
	failures: list[str] = []
	compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
	service = (compose.get("services") or {}).get("valhalla") or {}
	volumes = [str(item) for item in service.get("volumes") or []]

	if service.get("entrypoint") != ["valhalla_service"]:
		fail("valhalla runtime must bypass the mutating scripted entrypoint", failures)
	command = service.get("command") or []
	if command != ["/custom_files/valhalla.json", "${VALHALLA_THREADS:-4}"]:
		fail("valhalla runtime must serve the verified config directly", failures)
	if service.get("environment"):
		fail("valhalla serving runtime must not use scripted build environment flags", failures)
	if not any("maps/valhalla/current" in item and item.endswith(":/custom_files:ro") for item in volumes):
		fail("valhalla runtime must mount only maps/valhalla/current read-only", failures)
	for relative in (
		"infra/maps/scripts/build-valhalla.sh",
		"infra/maps/scripts/verify-valhalla.sh",
		"infra/maps/scripts/activate-valhalla.sh",
		"infra/maps/scripts/smoke-valhalla-global.py",
	):
		if not (root / relative).is_file():
			fail(f"missing global routing operations file: {relative}", failures)

	production = (root / "aos/utils/production_config.py").read_text(encoding="utf-8")
	if 'message="Valhalla routing must be enabled for production Maps."' not in production:
		fail("production configuration must require Valhalla routing", failures)

	docs = (root / "docs/features/maps/README.md").read_text(encoding="utf-8")
	for required in (
		"maps/valhalla/releases/",
		"smoke-valhalla-global.py",
		"maps_routing_enabled=1",
		"valhalla_service",
	):
		if required not in docs:
			fail(f"Maps documentation is missing routing contract marker: {required}", failures)

	if failures:
		print("Maps global routing policy validation failed:", file=sys.stderr)
		for message in failures:
			print(f"- {message}", file=sys.stderr)
		return 1

	print("Maps global Valhalla build/runtime/production policy: OK")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
