"""Cross-feature contract for the production-hardened public API boundary."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from aos.api.shared.transport import execute_endpoint


ROOT = Path(__file__).resolve().parents[2]


class TestFinalizedAPITransportBoundary(unittest.TestCase):
    FINALIZED_WRAPPERS = {
        "authentication": ("aos/api/v1/auth/__init__.py", 16),
        "accounts": ("aos/api/v1/accounts/__init__.py", 5),
        "localization": ("aos/api/v1/localization/__init__.py", 3),
        "media": ("aos/api/v1/media/__init__.py", 10),
        "catalog": ("aos/api/v1/catalog/__init__.py", 3),
    }

    def _source(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_shared_boundary_strips_only_frappe_transport_metadata(self):
        captured: dict[str, object] = {}

        def handler(**kwargs):
            captured.update(kwargs)
            return "ok"

        result = execute_endpoint(
            handler,
            {
                "cmd": "aos.api.v1.catalog.get_categories",
                "category": "Laptops",
                "unknown_field": True,
            },
        )
        self.assertEqual(result, "ok")
        self.assertEqual(captured, {"category": "Laptops", "unknown_field": True})

    def test_optional_failure_policy_does_not_create_a_second_transport_path(self):
        captured: dict[str, object] = {}

        def handler(**kwargs):
            captured.update(kwargs)
            raise RuntimeError("boom")

        def failure_policy(_handler, exc):
            return {"handled": str(exc)}

        result = execute_endpoint(
            handler,
            {"cmd": "aos.api.v1.auth.login", "identifier": "person@example.com"},
            on_unexpected_exception=failure_policy,
        )
        self.assertEqual(captured, {"identifier": "person@example.com"})
        self.assertEqual(result, {"handled": "boom"})

    def test_every_finalized_v1_wrapper_uses_the_same_executor(self):
        for feature, (relative, expected_endpoints) in self.FINALIZED_WRAPPERS.items():
            with self.subTest(feature=feature):
                source = self._source(relative)
                tree = ast.parse(source)
                whitelisted = [
                    node
                    for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and any(
                        isinstance(decorator, ast.Call)
                        and isinstance(decorator.func, ast.Attribute)
                        and isinstance(decorator.func.value, ast.Name)
                        and decorator.func.value.id == "frappe"
                        and decorator.func.attr == "whitelist"
                        for decorator in node.decorator_list
                    )
                ]
                self.assertEqual(len(whitelisted), expected_endpoints)
                self.assertIn(
                    "from aos.api.shared.transport import execute_endpoint as _execute_endpoint",
                    source,
                )
                self.assertEqual(source.count("_execute_endpoint("), expected_endpoints)
                self.assertNotIn("**client_kwargs(kwargs)", source)
                self.assertNotIn("**_client_kwargs(kwargs)", source)
                self.assertNotIn("_impl(**kwargs)", source)

    def test_authentication_exception_policy_is_layered_on_shared_executor(self):
        source = self._source("aos/api/v1/auth/__init__.py")
        contracts = self._source("aos/api/auth/contracts.py")
        self.assertEqual(source.count("on_unexpected_exception=_auth_exception_policy"), 16)
        self.assertNotIn("execute_auth_endpoint", source)
        self.assertNotIn("execute_auth_endpoint", contracts)
        self.assertIn("def handle_unexpected_auth_exception", contracts)
        self.assertNotIn('.pop("cmd"', contracts)
