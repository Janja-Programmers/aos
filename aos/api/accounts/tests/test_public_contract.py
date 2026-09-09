from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


class AccountsPublicContractTests(unittest.TestCase):
    def _source(self, relative: str) -> str:
        return (ROOT / relative).read_text()

    def test_versioned_accounts_surface_is_explicit_and_self_profile_is_separate(self):
        source = self._source("aos/api/v1/accounts/__init__.py")
        tree = ast.parse(source)
        whitelisted: set[str] = set()
        methods_by_name: dict[str, tuple[str, ...]] = {}
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "frappe"
                    and func.attr == "whitelist"
                ):
                    continue
                whitelisted.add(node.name)
                methods: tuple[str, ...] = ()
                for keyword in decorator.keywords:
                    if keyword.arg == "methods" and isinstance(keyword.value, (ast.List, ast.Tuple)):
                        methods = tuple(
                            element.value
                            for element in keyword.value.elts
                            if isinstance(element, ast.Constant) and isinstance(element.value, str)
                        )
                methods_by_name[node.name] = methods

        self.assertEqual(
            whitelisted,
            {
                "get_my_profile",
                "get_profile",
                "update_my_profile",
                "get_my_preference",
                "update_my_preference",
            },
        )
        self.assertEqual(methods_by_name["get_my_profile"], ("GET",))
        self.assertEqual(methods_by_name["get_profile"], ("GET",))
        self.assertEqual(methods_by_name["update_my_profile"], ("POST",))
        self.assertEqual(methods_by_name["get_my_preference"], ("GET",))
        self.assertEqual(methods_by_name["update_my_preference"], ("POST",))

    def test_profile_handlers_do_not_keep_ambiguous_public_update_alias(self):
        source = self._source("aos/api/accounts/profile.py")
        self.assertIn("def get_my_profile_impl", source)
        self.assertIn("def get_profile_impl", source)
        self.assertIn("def update_my_profile_impl", source)
        self.assertNotIn("def update_profile_impl", source)
        self.assertIn('code="ACCOUNT_ID_REQUIRED"', source)
        self.assertIn('_reject_unknown_fields(kwargs, allowed=set())', source)

    def test_accounts_documentation_is_single_authoritative_file(self):
        accounts_dir = ROOT / "docs/features/accounts"
        self.assertTrue((accounts_dir / "api.md").is_file())
        self.assertFalse((accounts_dir / "README.md").exists())
        feature_index = self._source("docs/features/README.md")
        self.assertIn("[Accounts](accounts/api.md)", feature_index)
        self.assertNotIn("accounts/README.md", feature_index)
