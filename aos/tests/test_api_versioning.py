"""Structural tests for the public AOS API v1 namespace."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1] / "api"
V1_ROOT = API_ROOT / "v1"
INTERNAL_FEATURES_TO_SKIP = {"internal", "shared", "v1", "__pycache__"}


def _feature_init_paths() -> list[Path]:
    return [
        path
        for path in sorted(API_ROOT.glob("*/__init__.py"))
        if path.parent.name not in INTERNAL_FEATURES_TO_SKIP
    ]


def _has_frappe_whitelist(function: ast.FunctionDef, source: str) -> bool:
    for decorator in function.decorator_list:
        segment = ast.get_source_segment(source, decorator) or ""
        if segment.strip().startswith("frappe.whitelist"):
            return True

    return False


def _public_function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }


def _whitelisted_function_names(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and _has_frappe_whitelist(node, source):
            names.add(node.name)

    return names


class TestAPIVersioning(unittest.TestCase):
    """Structural tests for the public versioned API contract."""

    def test_unversioned_feature_init_files_are_internal_package_markers(self):
        """Unversioned feature __init__ files must not expose wrapper callables."""

        violations: list[str] = []

        for path in _feature_init_paths():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))

            public_functions = _public_function_names(path)
            if public_functions:
                violations.append(f"{path}: public functions {sorted(public_functions)}")

            whitelisted = _whitelisted_function_names(path)
            if whitelisted:
                violations.append(f"{path}: whitelisted functions {sorted(whitelisted)}")

            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    violations.append(
                        f"{path}: imports are not allowed in internal package marker"
                    )

        self.assertFalse(
            violations,
            "Unversioned feature __init__ files are not clean package markers:\n"
            + "\n".join(violations),
        )

    def test_v1_modules_exist_for_every_api_feature_package(self):
        """Every API feature package must have an explicit v1 public namespace."""

        missing: list[str] = []

        for path in _feature_init_paths():
            feature = path.parent.name
            v1_path = V1_ROOT / feature / "__init__.py"
            if not v1_path.exists():
                missing.append(f"aos.api.v1.{feature}")

        self.assertFalse(
            missing,
            "Missing v1 public API modules:\n" + "\n".join(missing),
        )

    def test_v1_public_wrappers_are_whitelisted(self):
        """v1 wrapper functions must be whitelisted so Frappe can expose them."""

        violations: list[str] = []

        for path in sorted(V1_ROOT.glob("*/__init__.py")):
            all_functions = _public_function_names(path)
            whitelisted = _whitelisted_function_names(path)

            for name in sorted(all_functions - whitelisted):
                violations.append(f"{path}: {name}")

        self.assertFalse(
            violations,
            "v1 public wrappers are missing @frappe.whitelist:\n"
            + "\n".join(violations),
        )

    def test_v1_wrappers_import_implementation_modules_directly(self):
        """v1 wrappers should call implementation modules, not unversioned package wrappers."""

        violations: list[str] = []

        for path in sorted(V1_ROOT.glob("*/__init__.py")):
            feature = path.parent.name
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            disallowed_module = f"aos.api.{feature}"

            for node in tree.body:
                if not isinstance(node, ast.ImportFrom):
                    continue

                if node.module == disallowed_module:
                    violations.append(f"{path}: imports from {disallowed_module}")

        self.assertFalse(
            violations,
            "v1 wrappers still depend on unversioned package wrappers:\n"
            + "\n".join(violations),
        )

    def test_internal_api_modules_do_not_import_from_v1(self):
        """Internal implementation modules must not import public versioned wrappers."""

        violations: list[str] = []

        for path in sorted(API_ROOT.rglob("*.py")):
            relative_parts = path.relative_to(API_ROOT).parts
            if relative_parts[0] in {"v1", "shared", "__pycache__"}:
                continue

            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))

            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("aos.api.v1"):
                        violations.append(f"{path}: from {node.module} import ...")

                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("aos.api.v1"):
                            violations.append(f"{path}: import {alias.name}")

        self.assertFalse(
            violations,
            "Internal API implementation modules must not import from aos.api.v1:\n"
            + "\n".join(violations),
        )
