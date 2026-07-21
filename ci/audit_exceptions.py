from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

REQUIRED_FIELDS = {
	"advisory_id",
	"package",
	"locked_version",
	"lock",
	"justification",
	"owner",
	"expires_on",
	"removal_condition",
}


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--root", type=Path, required=True)
	parser.add_argument("--exceptions", type=Path, required=True)
	parser.add_argument("--lock", required=True)
	args = parser.parse_args()

	payload = json.loads(args.exceptions.read_text(encoding="utf-8"))
	if payload.get("schema_version") != 1:
		raise SystemExit("Unsupported vulnerability-exception schema version.")

	entries = payload.get("exceptions")
	if not isinstance(entries, list):
		raise SystemExit("Vulnerability exceptions must be a list.")

	seen: set[tuple[str, str]] = set()
	today = date.today()

	for index, entry in enumerate(entries):
		if not isinstance(entry, dict):
			raise SystemExit(f"Exception {index} must be an object.")

		missing = REQUIRED_FIELDS - entry.keys()
		if missing:
			raise SystemExit(f"Exception {index} is missing: {sorted(missing)}")

		for field in REQUIRED_FIELDS:
			if not isinstance(entry[field], str) or not entry[field].strip():
				raise SystemExit(f"Exception {index} has an invalid {field}.")

		key = (entry["lock"], entry["advisory_id"])
		if key in seen:
			raise SystemExit(f"Duplicate vulnerability exception: {key}")
		seen.add(key)

		expires_on = date.fromisoformat(entry["expires_on"])
		if expires_on < today:
			raise SystemExit(
				f"Vulnerability exception expired: {entry['advisory_id']} on {entry['expires_on']}"
			)

		lock_path = args.root / entry["lock"]
		lock_text = lock_path.read_text(encoding="utf-8")
		expected_pin = f"{entry['package']}=={entry['locked_version']}"
		if not any(
			line == expected_pin or line.startswith(f"{expected_pin} ") for line in lock_text.splitlines()
		):
			raise SystemExit(
				f"Exception {entry['advisory_id']} does not match {expected_pin} in {entry['lock']}."
			)

		if entry["lock"] == args.lock:
			print(entry["advisory_id"])

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
