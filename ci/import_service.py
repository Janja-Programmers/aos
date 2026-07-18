from __future__ import annotations

import importlib
import pkgutil

import app


def main() -> int:
	modules = ["app"]
	modules.extend(module.name for module in pkgutil.walk_packages(app.__path__, prefix="app."))
	for module_name in sorted(modules):
		importlib.import_module(module_name)
	print(f"Imported {len(modules)} modules without model initialization.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
