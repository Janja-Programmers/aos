from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

PROHIBITED_PARTS = {
	".coverage",
	".mypy_cache",
	".pytest_cache",
	".ruff_cache",
	".tox",
	".venv",
	"__pycache__",
	"coverage",
	"htmlcov",
	"node_modules",
	"venv",
}
PROHIBITED_SUFFIXES = {
	".bak",
	".backup",
	".dump",
	".key",
	".mbtiles",
	".p12",
	".pem",
	".pfx",
	".pyc",
	".sql",
}
SERVICE_ACCOUNT = re.compile(r"(?:service[-_ ]?account|firebase[-_ ]?admin).*(?:\.json)$", re.I)


def tracked_files(root: Path) -> list[str]:
	result = subprocess.run(
		["git", "-C", str(root), "ls-files", "-z"],
		check=True,
		capture_output=True,
	)
	return [item.decode() for item in result.stdout.split(b"\0") if item]


def reason(path_text: str) -> str | None:
	path = PurePosixPath(path_text)
	lower_parts = {part.lower() for part in path.parts}
	name = path.name.lower()

	if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
		return "environment/secret file"
	if lower_parts & PROHIBITED_PARTS:
		return "generated cache, environment, or dependency directory"
	if path.suffix.lower() in PROHIBITED_SUFFIXES:
		return "credential, database, backup, or generated data extension"
	if name.endswith(".osm.pbf"):
		return "generated map dataset"
	if any(part in {"models", "docker-volumes", "volumes"} for part in lower_parts):
		return "downloaded model or Docker volume data"
	if SERVICE_ACCOUNT.search(name) and ".example." not in name:
		return "service-account credential filename"
	if name in {"id_rsa", "id_ed25519", "credentials", "credentials.json"}:
		return "credential or private-key filename"
	return None


def main() -> int:
	root = Path(__file__).resolve().parents[1]
	findings = [(path, reason(path)) for path in tracked_files(root)]
	findings = [(path, why) for path, why in findings if why]
	if findings:
		print("Repository hygiene failed (values are intentionally not displayed):", file=sys.stderr)
		for path, why in findings:
			print(f"- {path}: {why}", file=sys.stderr)
		return 1
	print("Tracked-file hygiene policy: OK")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
