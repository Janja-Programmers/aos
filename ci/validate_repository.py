from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from pathlib import Path

import yaml

TEXT_SUFFIXES = {
	".conf",
	".css",
	".env",
	".html",
	".ini",
	".js",
	".json",
	".py",
	".service",
	".sh",
	".template",
	".timer",
	".toml",
	".ts",
	".yaml",
	".yml",
}
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__"}
CONFLICT = re.compile(r"^(<<<<<<< |=======\s*$|>>>>>>> )", re.MULTILINE)
RAW_DDL = re.compile(r"^\s*(ALTER|CREATE|DROP|TRUNCATE|RENAME)\b", re.IGNORECASE)


class UniqueKeyLoader(yaml.SafeLoader):
	pass


def construct_mapping(loader, node, deep=False):
	keys = set()
	for key_node, _value_node in node.value:
		if key_node.tag == "tag:yaml.org,2002:merge":
			continue
		key = loader.construct_object(key_node, deep=deep)
		if key in keys:
			raise ValueError(f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}")
		keys.add(key)
	return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


UniqueKeyLoader.add_constructor(
	yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
	construct_mapping,
)


def files(root: Path):
	for path in sorted(root.rglob("*")):
		if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
			continue
		yield path


def is_text_candidate(path: Path) -> bool:
	return path.name in {"Dockerfile", "Makefile"} or path.suffix.lower() in TEXT_SUFFIXES


def read_text(path: Path) -> str | None:
	if not is_text_candidate(path):
		return None
	try:
		return path.read_text(encoding="utf-8")
	except UnicodeDecodeError:
		return None


def has_debug_statement(path: Path, text: str) -> bool:
	if path.suffix != ".py":
		return False
	try:
		tree = ast.parse(text, filename=str(path))
	except SyntaxError:
		return False
	for node in ast.walk(tree):
		if not isinstance(node, ast.Call):
			continue
		if isinstance(node.func, ast.Name) and node.func.id == "breakpoint":
			return True
		if isinstance(node.func, ast.Attribute) and node.func.attr == "set_trace":
			return True
	return False


def _attribute_path(node: ast.AST) -> tuple[str, ...]:
	parts: list[str] = []
	while isinstance(node, ast.Attribute):
		parts.append(node.attr)
		node = node.value
	if isinstance(node, ast.Name):
		parts.append(node.id)
	return tuple(reversed(parts))


def _string_template(node: ast.AST | None) -> str | None:
	if isinstance(node, ast.Constant) and isinstance(node.value, str):
		return node.value
	if isinstance(node, ast.JoinedStr):
		parts: list[str] = []
		for value in node.values:
			if isinstance(value, ast.Constant) and isinstance(value.value, str):
				parts.append(value.value)
			else:
				parts.append("{expr}")
		return "".join(parts)
	return None


SENSITIVE_REPR_EXACT = frozenset(
	{
		"password",
		"passwd",
		"pwd",
		"secret",
		"secret_key",
		"api_secret",
		"api_key",
		"access_key",
		"private_key",
		"client_secret",
		"service_secret",
		"callback_secret",
		"request_secret",
		"internal_secret",
		"classification_secret",
		"sid",
		"session_id",
		"token",
		"x_aos_signature",
		"livekit_keys",
	}
)


def _is_sensitive_repr_field(name: str) -> bool:
	normalized = str(name or "").strip().lower().replace("-", "_")
	return normalized in SENSITIVE_REPR_EXACT or normalized.endswith(
		("_secret", "_token", "_password", "_passwd", "_pwd")
	)


def _terminal_name(node: ast.AST) -> str:
	if isinstance(node, ast.Name):
		return node.id
	if isinstance(node, ast.Attribute):
		return node.attr
	return ""


def _has_repr_false(value: ast.AST | None, constructor: str) -> bool:
	if not isinstance(value, ast.Call) or _terminal_name(value.func) != constructor:
		return False
	for keyword in value.keywords:
		if keyword.arg == "repr" and isinstance(keyword.value, ast.Constant):
			return keyword.value.value is False
	return False


def unsafe_sensitive_repr_fields(path: Path, text: str) -> list[tuple[int, str, str]]:
	"""Find secret-bearing dataclass/Pydantic fields that can leak through repr()."""
	if path.suffix != ".py":
		return []
	try:
		tree = ast.parse(text, filename=str(path))
	except SyntaxError:
		return []

	offenders: list[tuple[int, str, str]] = []
	for node in ast.walk(tree):
		if not isinstance(node, ast.ClassDef):
			continue
		is_dataclass = any(_terminal_name(decorator) == "dataclass" for decorator in node.decorator_list)
		is_pydantic = any(_terminal_name(base) in {"BaseModel", "BaseSettings"} for base in node.bases)
		if not (is_dataclass or is_pydantic):
			continue
		constructor = "field" if is_dataclass else "Field"
		for statement in node.body:
			if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
				continue
			field_name = statement.target.id
			if not _is_sensitive_repr_field(field_name):
				continue
			if not _has_repr_false(statement.value, constructor):
				offenders.append((statement.lineno, node.name, field_name))
	return offenders


def raw_patch_ddl_lines(path: Path, text: str) -> list[int]:
	if path.suffix != ".py" or "aos/patches" not in path.as_posix():
		return []
	try:
		tree = ast.parse(text, filename=str(path))
	except SyntaxError:
		return []
	lines: list[int] = []
	for node in ast.walk(tree):
		if not isinstance(node, ast.Call) or _attribute_path(node.func) != ("frappe", "db", "sql"):
			continue
		query_node = (
			node.args[0]
			if node.args
			else next(
				(keyword.value for keyword in node.keywords if keyword.arg == "query"),
				None,
			)
		)
		query = _string_template(query_node)
		if query and RAW_DDL.search(query):
			lines.append(node.lineno)
	return lines


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("root", nargs="?", default=Path(__file__).resolve().parents[1])
	parser.add_argument("--yaml-only", action="store_true")
	args = parser.parse_args()
	root = Path(args.root).resolve()
	errors: list[str] = []

	for path in files(root):
		if args.yaml_only and path.suffix not in {".yaml", ".yml"}:
			continue
		relative = path.relative_to(root)
		text = read_text(path)
		if text is None:
			continue

		if CONFLICT.search(text):
			errors.append(f"{relative}: conflict marker")
		if has_debug_statement(path, text):
			errors.append(f"{relative}: debugger statement")
		for line_number in raw_patch_ddl_lines(path, text):
			errors.append(
				f"{relative}:{line_number}: raw DDL through frappe.db.sql; "
				"use frappe.db.add_index/add_unique or frappe.db.sql_ddl"
			)
		for line_number, class_name, field_name in unsafe_sensitive_repr_fields(path, text):
			errors.append(
				f"{relative}:{line_number}: secret-bearing field "
				f"{class_name}.{field_name} must declare repr=False"
			)
		# The AOS Frappe app is intentionally not mass-reformatted in this
		# checkpoint. Conflict/debug/structured-file checks still cover it;
		# whitespace enforcement applies to all checkpoint-owned/non-app files.
		frappe_source = relative.parts and relative.parts[0] == "aos"
		if not frappe_source and path.suffix.lower() not in {".md", ".csv", ".svg"}:
			for line_number, line in enumerate(text.splitlines(), start=1):
				if line.rstrip(" \t") != line:
					errors.append(f"{relative}:{line_number}: trailing whitespace")

		try:
			if path.suffix == ".json":
				json.loads(text, object_pairs_hook=_reject_json_duplicates(relative))
			elif path.suffix == ".toml":
				tomllib.loads(text)
			elif path.suffix in {".yaml", ".yml"}:
				yaml.load(text, Loader=UniqueKeyLoader)
		except Exception as exc:
			errors.append(f"{relative}: {exc}")

	if errors:
		print("Repository validation failed:", file=sys.stderr)
		for error in errors:
			print(f"- {error}", file=sys.stderr)
		return 1
	print(
		"JSON, TOML, YAML, duplicate keys, conflicts, debug statements, patch DDL, "
		"secret-safe reprs, and whitespace: OK"
	)
	return 0


def _reject_json_duplicates(path: Path):
	def hook(pairs):
		result = {}
		for key, value in pairs:
			if key in result:
				raise ValueError(f"duplicate JSON key {key!r} in {path}")
			result[key] = value
		return result

	return hook


if __name__ == "__main__":
	raise SystemExit(main())
