from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ALLOWED_POLICIES = {
	"baseline_guest",
	"baseline_authenticated",
	"signed_internal_exempt",
	"private_metrics_exempt",
	"infrastructure_health_dedicated",
}


def discover(root: Path) -> dict[str, str]:
	found: dict[str, str] = {}
	for path in sorted((root / "aos" / "api").rglob("*.py")):
		tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
		module = ".".join(path.relative_to(root).with_suffix("").parts)
		for node in tree.body:
			if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
				continue
			for decorator in node.decorator_list:
				call = decorator if isinstance(decorator, ast.Call) else None
				function = call.func if call else decorator
				name = (
					function.attr
					if isinstance(function, ast.Attribute)
					else function.id
					if isinstance(function, ast.Name)
					else ""
				)
				if name != "whitelist":
					continue
				guest = False
				if call:
					for keyword in call.keywords:
						if keyword.arg == "allow_guest" and isinstance(keyword.value, ast.Constant):
							guest = bool(keyword.value.value)
				found[f"{module}.{node.name}"] = "guest" if guest else "authenticated"
				break
	return found


def main() -> int:
	root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
	registry_path = root / "ci" / "public-endpoint-rate-limits.json"
	entries = json.loads(registry_path.read_text(encoding="utf-8"))
	registry = {item["endpoint"]: item for item in entries}
	actual = discover(root)
	missing = sorted(set(actual) - set(registry))
	stale = sorted(set(registry) - set(actual))
	errors: list[str] = []
	if missing:
		errors.append("Public endpoints missing a reviewed rate-limit policy: " + ", ".join(missing))
	if stale:
		errors.append("Stale endpoint registry entries: " + ", ".join(stale))
	for endpoint, access in actual.items():
		entry = registry.get(endpoint) or {}
		if entry.get("access") != access:
			errors.append(f"Access classification mismatch for {endpoint}")
		if entry.get("policy") not in ALLOWED_POLICIES:
			errors.append(f"Invalid or missing policy for {endpoint}")
		if not str(entry.get("rationale") or "").strip():
			errors.append(f"Missing review rationale for {endpoint}")
		if entry.get("policy", "").endswith("exempt"):
			if endpoint.endswith("handle_callback") and entry.get("policy") != "signed_internal_exempt":
				errors.append(f"Callback exemption is not signed-internal for {endpoint}")
	if errors:
		print("\n".join(errors), file=sys.stderr)
		return 1
	print(f"Validated {len(actual)} whitelisted endpoint rate-limit policies.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
