"""Git-aware source inventory for repository-wide quality checks.

Include staged/tracked sources and non-ignored untracked files, but never walk
runtime artifacts (downloaded models, caches, map data, environments).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def source_files(root: Path):
	"""Yield AOS-owned files without following symlinks outside the checkout."""
	result = subprocess.run(
		["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--"],
		check=True,
		capture_output=True,
	)
	relative_paths = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
	for relative in sorted(set(relative_paths)):
		if not relative:
			continue
		path = root / relative
		if not path.is_symlink() and path.is_file():
			yield path
