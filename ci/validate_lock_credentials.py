from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PROHIBITED = (
	re.compile(r"(?i)https?://[^/\s:@]+:[^@\s/]+@"),
	re.compile(r"(?i)https?://[^\s]+[?&](?:access[_-]?token|api[_-]?key|password|secret|token)=[^&\s]+"),
	re.compile(r"(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,})"),
)


def tracked_locks(root: Path) -> list[Path]:
	result = subprocess.run(
		["git", "-C", str(root), "ls-files", "-z", "*.lock"], check=True, capture_output=True
	)
	return [root / item.decode() for item in result.stdout.split(b"\0") if item]


def main() -> int:
	root = Path(__file__).resolve().parents[1]
	failures: list[str] = []
	for path in tracked_locks(root):
		for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
			if any(pattern.search(line) for pattern in PROHIBITED):
				failures.append(f"{path.relative_to(root)}:{line_number}")
	if failures:
		print("Credential-bearing URL/token pattern found in lock file (values suppressed):", file=sys.stderr)
		for failure in failures:
			print(f"- {failure}", file=sys.stderr)
		return 1
	print(f"Lock credential policy: OK ({len(tracked_locks(root))} tracked lock files)")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
