from __future__ import annotations

import ast
from pathlib import Path
import unittest


HELPERS = Path(__file__).with_name("feature_test_helpers.py")
TRANSACTION_LOCAL_BUILDERS = {
    "make_system_user",
    "make_user",
    "make_category",
    "make_location",
    "make_media",
    "make_seller",
    "make_ad",
    "make_conversation",
    "make_short",
    "make_report_reason",
    "make_live",
}


def _call_name(node: ast.Call) -> str:
    parts: list[str] = []
    current = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


class TestFixtureTransactionSafety(unittest.TestCase):
    """Prevent ordinary DB fixtures from becoming durable test pollution."""

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(HELPERS.read_text(encoding="utf-8"), filename=str(HELPERS))
        cls.mixin = next(
            node
            for node in cls.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "AOSFeatureTestMixin"
        )
        cls.methods = {
            node.name: node
            for node in cls.mixin.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_ordinary_fixture_builders_never_commit(self):
        missing = TRANSACTION_LOCAL_BUILDERS.difference(self.methods)
        self.assertFalse(missing, f"Missing guarded fixture builders: {sorted(missing)}")

        offenders: dict[str, list[int]] = {}
        for name in sorted(TRANSACTION_LOCAL_BUILDERS):
            method = self.methods[name]
            lines = [
                node.lineno
                for node in ast.walk(method)
                if isinstance(node, ast.Call) and _call_name(node) == "frappe.db.commit"
            ]
            if lines:
                offenders[name] = lines

        self.assertFalse(
            offenders,
            "Ordinary feature fixtures must remain transaction-local; "
            f"remove frappe.db.commit() from: {offenders}",
        )

    def test_cleanup_rolls_back_before_compensating_deletes(self):
        cleanup = self.methods["cleanup_feature_rows"]
        calls = [
            (node.lineno, _call_name(node))
            for node in ast.walk(cleanup)
            if isinstance(node, ast.Call)
            and _call_name(node) in {"frappe.db.rollback", "frappe.db.sql", "frappe.delete_doc"}
        ]
        calls.sort()
        self.assertTrue(calls, "cleanup_feature_rows must contain transaction cleanup calls")
        self.assertEqual(
            calls[0][1],
            "frappe.db.rollback",
            "cleanup_feature_rows must rollback uncommitted fixtures before compensating cleanup",
        )

    def test_shared_ad_fixture_is_covered_by_the_no_commit_guard(self):
        make_ad = self.methods["make_ad"]
        source = ast.get_source_segment(HELPERS.read_text(encoding="utf-8"), make_ad) or ""
        for dependency in ("make_category", "make_location", "make_seller", "make_media"):
            self.assertIn(f"self.{dependency}(", source)
        self.assertNotIn("frappe.db.commit", source)


    def test_request_rollback_helper_uses_savepoint_without_committing(self):
        helper = self.methods["request_rollback_savepoint"]
        source = ast.get_source_segment(HELPERS.read_text(encoding="utf-8"), helper) or ""
        self.assertIn("frappe.db.savepoint", source)
        self.assertIn("save_point=save_point", source)
        self.assertNotIn("frappe.db.commit", source)

    def test_live_fixture_clears_external_hot_state_before_use(self):
        make_live = self.methods["make_live"]
        source = ast.get_source_segment(HELPERS.read_text(encoding="utf-8"), make_live) or ""
        self.assertIn('self._track_created("created_live_names", live.name)', source)
        self.assertIn("self._clear_live_ephemeral_state(live.name)", source)
        self.assertNotIn("frappe.db.commit", source)

    def test_make_short_callers_do_not_commit_their_fixture_transaction(self):
        offenders: dict[str, list[str]] = {}
        tests_root = HELPERS.parent.parent

        for path in sorted(tests_root.rglob("*.py")):
            if path == HELPERS or path == Path(__file__):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                calls = [child for child in ast.walk(node) if isinstance(child, ast.Call)]
                calls_make_short = any(
                    _call_name(call) in {"self.make_short", "make_short"}
                    for call in calls
                )
                commits = [
                    call.lineno
                    for call in calls
                    if _call_name(call) == "frappe.db.commit"
                ]
                if calls_make_short and commits:
                    relative = str(path.relative_to(tests_root.parent))
                    offenders.setdefault(relative, []).append(
                        f"{node.name}: commit lines {commits}"
                    )

        self.assertFalse(
            offenders,
            "Tests using ordinary make_short fixtures must not commit the fixture transaction. "
            f"Offenders: {offenders}",
        )

    def test_make_ad_callers_do_not_commit_their_fixture_transaction(self):
        offenders: dict[str, list[str]] = {}
        tests_root = HELPERS.parent.parent

        for path in sorted(tests_root.rglob("*.py")):
            if path == HELPERS or path == Path(__file__):
                continue

            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue

                calls = [
                    child
                    for child in ast.walk(node)
                    if isinstance(child, ast.Call)
                ]
                calls_make_ad = any(
                    _call_name(call) in {"self.make_ad", "make_ad"}
                    for call in calls
                )
                commits = [
                    call.lineno
                    for call in calls
                    if _call_name(call) == "frappe.db.commit"
                ]
                if calls_make_ad and commits:
                    relative = str(path.relative_to(tests_root.parent))
                    offenders.setdefault(relative, []).append(
                        f"{node.name}: commit lines {commits}"
                    )

        self.assertFalse(
            offenders,
            "Tests using ordinary make_ad fixtures must not commit the fixture transaction. "
            "Use a dedicated explicitly committed fixture path only for genuine cross-transaction tests. "
            f"Offenders: {offenders}",
        )

    def test_user_action_uniqueness_fixtures_do_not_commit(self):
        source = Path(__file__).with_name("test_user_action_uniqueness.py").read_text(encoding="utf-8")
        self.assertNotIn("frappe.db.commit()", source)
        self.assertNotIn("file_key", source)

    def test_committed_fixture_cleanup_rolls_back_before_delete_commit(self):
        source = Path(__file__).with_name("outbox_fixtures.py").read_text(encoding="utf-8")
        tree = ast.parse(source, filename="outbox_fixtures.py")
        fn = next(
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "cleanup_committed_records"
        )
        calls = [
            (node.lineno, _call_name(node))
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and _call_name(node) in {"frappe.db.rollback", "frappe.db.delete", "frappe.db.commit"}
        ]
        calls.sort()
        self.assertTrue(calls)
        self.assertEqual(calls[0][1], "frappe.db.rollback")
        self.assertIn("frappe.db.commit", [name for _, name in calls])

    def test_shorts_compensating_cleanup_uses_current_schema_and_exact_ids(self):
        source = HELPERS.read_text(encoding="utf-8")
        self.assertNotIn("tabAOS Short` WHERE file_key", source)
        self.assertIn('self._track_created("created_short_names", short.name)', source)
        self.assertIn('"AOS Short Repost"', source)
        self.assertIn('"AOS Short Feedback"', source)
        self.assertIn('"AOS Short Moderation Decision"', source)
        self.assertIn('"AOS Video Processing Job"', source)

    def test_short_hot_state_cleanup_covers_daily_redis_key(self):
        hot_metrics = Path(__file__).parents[1] / "services" / "shorts" / "hot_metrics.py"
        source = hot_metrics.read_text(encoding="utf-8")
        self.assertIn("cache.delete_value(daily_key(short_id))", source)


    def test_leaked_fixture_cleanup_is_explicit_and_not_scheduled(self):
        cleanup = Path(__file__).with_name("cleanup_leaked_test_artifacts.py").read_text(encoding="utf-8")
        self.assertIn('DELETE_AOS_TEST_ARTIFACTS', cleanup)
        self.assertIn('frappe.db.rollback()', cleanup)
        self.assertNotIn('@frappe.whitelist', cleanup)



if __name__ == "__main__":
    unittest.main()
