from __future__ import annotations

import re
import sys
from pathlib import Path

PATH = re.compile(r"(?<![\w/])(?:ci|infra|scripts)/[A-Za-z0-9_.${}/-]+")
GENERATED_LOCAL_PATHS = {
	"infra/load-testing/k6/env.local.sh",
	"infra/maps/manifest.env",
}


def main() -> int:
	root = Path(__file__).resolve().parents[1]
	documents = [root / "README.md", *sorted((root / "docs").rglob("*.md"))]
	missing: list[str] = []
	checked: set[str] = set()
	for document in documents:
		for line_number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
			for match in PATH.findall(line):
				relative = match.rstrip(".,:;)`\"'")
				if "$" in relative or "{" in relative or relative in GENERATED_LOCAL_PATHS:
					continue
				checked.add(relative)
				if not (root / relative).exists():
					missing.append(f"{document.relative_to(root)}:{line_number}: {relative}")
	if missing:
		print("Documented repository paths are missing:", file=sys.stderr)
		for finding in missing:
			print(f"- {finding}", file=sys.stderr)
		return 1
	print(f"Documented repository paths: OK ({len(checked)} unique paths)")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
