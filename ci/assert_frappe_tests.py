from __future__ import annotations

import ast
from pathlib import Path


def count_tests(root: Path) -> tuple[int, int]:
	files = 0
	tests = 0
	for path in sorted(root.rglob("test_*.py")):
		tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
		file_tests = 0
		for node in ast.walk(tree):
			if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
				file_tests += 1
		if file_tests:
			files += 1
			tests += file_tests
	return files, tests


def main() -> int:
	repository = Path(__file__).resolve().parents[1]
	files, tests = count_tests(repository / "aos")
	if not files or not tests:
		raise SystemExit("No Frappe test functions were collected from the AOS source tree.")
	print(f"Frappe collection assertion: {tests} test methods/functions in {files} files.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
