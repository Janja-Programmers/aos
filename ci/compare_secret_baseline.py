from __future__ import annotations

import json
import sys
from pathlib import Path


def fingerprints(document: dict, *, require_review: bool) -> set[tuple[str, str, str]]:
	result: set[tuple[str, str, str]] = set()
	for path, findings in document.get("results", {}).items():
		for finding in findings:
			if require_review and finding.get("is_secret") is not False:
				raise ValueError(f"baseline finding is not reviewed: {path}:{finding.get('line_number')}")
			result.add((path, finding["type"], finding["hashed_secret"]))
	return result


def main() -> int:
	if len(sys.argv) != 3:
		raise SystemExit("usage: compare_secret_baseline.py BASELINE CURRENT_REPORT")
	baseline_path, report_path = map(Path, sys.argv[1:])
	baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
	current = json.loads(report_path.read_text(encoding="utf-8"))
	try:
		approved = fingerprints(baseline, require_review=True)
	except ValueError as exc:
		print(f"Secret baseline review failed: {exc}", file=sys.stderr)
		return 1
	detected = fingerprints(current, require_review=False)
	new = detected - approved
	stale = approved - detected
	if new or stale:
		print(
			"Secret baseline mismatch; values are suppressed. "
			f"new={len(new)}, stale={len(stale)}. Review and regenerate .secrets.baseline.",
			file=sys.stderr,
		)
		return 1
	print(f"Secret scan: OK ({len(detected)} reviewed synthetic/hash findings; entropy plugins enabled)")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
