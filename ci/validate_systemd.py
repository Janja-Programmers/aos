from __future__ import annotations

import configparser
import sys
from pathlib import Path

REQUIRED = {
	".service": {"Unit", "Service"},
	".timer": {"Unit", "Timer", "Install"},
}


def main() -> int:
	root = Path(__file__).resolve().parents[1]
	failures: list[str] = []
	units = sorted((root / "infra" / "systemd").iterdir())
	for path in units:
		if path.suffix not in REQUIRED:
			continue
		parser = configparser.ConfigParser(interpolation=None, strict=True)
		try:
			parser.read(path, encoding="utf-8")
		except (configparser.Error, OSError) as exc:
			failures.append(f"{path.relative_to(root)}: {exc}")
			continue
		missing = REQUIRED[path.suffix] - set(parser.sections())
		if missing:
			failures.append(f"{path.relative_to(root)}: missing sections {sorted(missing)}")
		if path.suffix == ".service" and "ExecStart" not in parser["Service"]:
			failures.append(f"{path.relative_to(root)}: Service.ExecStart is required")
		if path.suffix == ".timer" and "OnCalendar" not in parser["Timer"]:
			failures.append(f"{path.relative_to(root)}: Timer.OnCalendar is required")

	if failures:
		print("systemd unit validation failed:", file=sys.stderr)
		for failure in failures:
			print(f"- {failure}", file=sys.stderr)
		return 1
	print(f"systemd unit syntax: OK ({len(units)} files inspected)")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
