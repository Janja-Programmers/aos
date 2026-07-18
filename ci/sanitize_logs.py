from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERNS = (
	re.compile(r"(?i)(password|passwd|token|secret)(\s*[:=]\s*)\S+"),
	re.compile(r"(?i)(--(?:admin|db-root)-password(?:=|\s+))\S+"),
)


def main() -> int:
	if len(sys.argv) != 3:
		raise SystemExit("usage: sanitize_logs.py INPUT OUTPUT")
	text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
	for pattern in PATTERNS:
		text = pattern.sub(
			lambda match: (
				f"{match.group(1)}{match.group(2) if match.lastindex and match.lastindex > 1 else ''}[REDACTED]"
			),
			text,
		)
	Path(sys.argv[2]).write_text(text, encoding="utf-8")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
