"""Structural tests for API package wrapper modules."""

from __future__ import annotations

import ast
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1] / "api"


def _statement_body_without_docstring(function: ast.FunctionDef) -> list[ast.stmt]:
    body = list(function.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _is_simple_impl_return(function: ast.FunctionDef) -> bool:
    body = _statement_body_without_docstring(function)
    return (
        len(body) == 1
        and isinstance(body[0], ast.Return)
        and isinstance(body[0].value, ast.Call)
    )


def test_api_init_modules_only_expose_simple_wrappers():
    """Feature API __init__ modules should not contain business logic."""

    violations: list[str] = []
    for path in sorted(API_ROOT.rglob("__init__.py")):
        relative = path.relative_to(API_ROOT)
        if relative.parts[0] == "shared":
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not _is_simple_impl_return(node):
                violations.append(f"{path}: {node.name}")

    assert not violations, "Business logic found in API wrapper module(s):\n" + "\n".join(violations)
