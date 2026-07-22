from __future__ import annotations

import ast
from pathlib import Path

REQUIRED_FOUNDATION_TESTS = {
	"test_transactional_outbox_recovery.py",
	"test_outbox_recovery_dispatch_all_services.py",
	"test_callback_atomicity_all_services.py",
	"test_outbox_backfill.py",
	"test_operational_metrics.py",
	"test_backup_readiness.py",
	"test_migration_preflight.py",
	"test_outbox_final_lifecycle_patch.py",
}
FORBIDDEN_FRAPPE_TEST_IMPORTS = {"pytest"}

REQUIRED_MEDIA_TEST_FILES = {
	"__init__.py",
	"test_category_integration.py",
	"test_content_validation.py",
	"test_purpose_policies.py",
	"test_runtime_config.py",
	"test_migration_patch.py",
	"test_service.py",
}

REQUIRED_ACCOUNTS_TEST_FILES = {
    "__init__.py",
    "test_consumer_privacy.py",
    "test_identity.py",
    "test_lifecycle.py",
    "test_migration.py",
    "test_preferences.py",
    "test_profile.py",
    "test_serializers.py",
    "test_validation.py",
}
LEGACY_ACCOUNTS_TEST_FILES = {
    "test_accounts.py", "test_account_profile.py", "test_account_preferences.py",
}

LEGACY_MEDIA_TEST_FILES = {
	"test_category_media_hooks.py",
	"test_media_content_validation.py",
	"test_media_purpose_policies.py",
	"test_media_runtime_config.py",
	"test_media_service.py",
}

REQUIRED_BEHAVIORAL_TESTS = {
	"test_outbox_recovery_dispatch_all_services.py": {
		"test_callback_timeout_redispatches_all_five_services_and_replay_completes",
		"test_expired_publisher_lease_for_processing_job_redispatches_all_services",
		"test_callback_timeout_exhaustion_enters_manual_review_for_every_service_type",
	},
	"test_callback_atomicity_all_services.py": {
		"test_valid_success_and_duplicate_success_are_atomic_and_idempotent",
		"test_valid_failure_and_duplicate_failure_are_atomic_and_idempotent",
		"test_service_job_save_and_outbox_save_failures_roll_back_every_service",
		"test_operator_work_replay_changes_generation_and_rejects_old_callbacks",
	},
	"test_outbox_backfill.py": {
		"test_each_doctype_backfills_retryable_states_and_skips_terminal_states",
		"test_mixed_all_service_patch_execution_is_idempotent_batched_and_rollback_safe",
	},
	"test_transactional_outbox_recovery.py": {
		"test_terminal_failure_callback_is_not_redispatched",
		"test_valid_callback_remains_eligible_while_publisher_holds_lease",
		"test_companion_generation_floor_advances_next_proposal",
		"test_reconciliation_exhaustion_moves_to_manual_review",
		"test_callback_already_completed_repairs_terminal_failure_outbox",
	},
	"test_outbox_final_lifecycle_patch.py": {
		"test_patch_terminalizes_failure_callback_and_is_idempotent",
		"test_patch_normalizes_legacy_claim_and_exhausted_reconciliation",
		"test_outbox_conflict_before_action_assignment_preserves_original_error",
		"test_system_manager_work_replay_reopens_same_job_and_outbox",
	},
}


def _parse(path: Path) -> ast.Module:
	return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _test_names(path: Path) -> set[str]:
	tree = _parse(path)
	return {
		node.name
		for node in ast.walk(tree)
		if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
	}


def _forbidden_imports(path: Path) -> set[str]:
	forbidden: set[str] = set()
	for node in ast.walk(_parse(path)):
		if isinstance(node, ast.Import):
			for alias in node.names:
				root = alias.name.split(".", maxsplit=1)[0]
				if root in FORBIDDEN_FRAPPE_TEST_IMPORTS:
					forbidden.add(root)
		elif isinstance(node, ast.ImportFrom) and node.module:
			root = node.module.split(".", maxsplit=1)[0]
			if root in FORBIDDEN_FRAPPE_TEST_IMPORTS:
				forbidden.add(root)
	return forbidden


def count_tests(root: Path) -> tuple[int, int]:
	files = 0
	tests = 0
	for path in sorted(root.rglob("test_*.py")):
		file_tests = len(_test_names(path))
		if file_tests:
			files += 1
			tests += file_tests
	return files, tests


def main() -> int:
	repository = Path(__file__).resolve().parents[1]
	test_root = repository / "aos"
	media_test_root = test_root / "api" / "media" / "tests"
	accounts_test_root = test_root / "api" / "accounts" / "tests"
	missing_accounts_files = (
		sorted(
			REQUIRED_ACCOUNTS_TEST_FILES
			- {path.name for path in accounts_test_root.iterdir() if path.is_file()}
		)
		if accounts_test_root.is_dir()
		else sorted(REQUIRED_ACCOUNTS_TEST_FILES)
	)
	if missing_accounts_files:
		raise SystemExit(
			"Accounts feature tests must live under aos/api/accounts/tests: "
			+ str(missing_accounts_files)
		)
	legacy_accounts_paths = sorted(
		str(path.relative_to(repository))
		for path in (test_root / "tests").glob("test_*.py")
		if path.name in LEGACY_ACCOUNTS_TEST_FILES
	)
	if legacy_accounts_paths:
		raise SystemExit(
			"Accounts-owned tests must not live in the generic aos/tests package: "
			+ str(legacy_accounts_paths)
		)
	missing_media_files = (
		sorted(
			REQUIRED_MEDIA_TEST_FILES
			- {path.name for path in media_test_root.iterdir() if path.is_file()}
		)
		if media_test_root.is_dir()
		else sorted(REQUIRED_MEDIA_TEST_FILES)
	)
	if missing_media_files:
		raise SystemExit(
			"Media feature tests must live under aos/api/media/tests: "
			+ str(missing_media_files)
		)
	legacy_media_paths = sorted(
		str(path.relative_to(repository))
		for path in (test_root / "tests").glob("test_*.py")
		if path.name in LEGACY_MEDIA_TEST_FILES
	)
	if legacy_media_paths:
		raise SystemExit(
			"Media-owned tests must not live in the generic aos/tests package: "
			+ str(legacy_media_paths)
		)
	media_fixture = media_test_root / "fixtures" / "valid_64x64.png"
	if not media_fixture.is_file():
		raise SystemExit(f"Required Media test fixture is missing: {media_fixture}")
	files, tests = count_tests(test_root)
	test_paths = sorted(test_root.rglob("test_*.py"))
	paths = {path.name: path for path in test_paths}
	unsupported = {
		str(path.relative_to(repository)): sorted(imports)
		for path in test_paths
		if (imports := _forbidden_imports(path))
	}
	if unsupported:
		raise SystemExit(
			"Frappe-discovered tests must not require pytest-only imports: "
			+ "; ".join(f"{path}: {imports}" for path, imports in unsupported.items())
		)
	missing = sorted(REQUIRED_FOUNDATION_TESTS - paths.keys())
	if missing:
		raise SystemExit(f"Required production-foundation Frappe tests are missing: {missing}")
	for filename, required in REQUIRED_BEHAVIORAL_TESTS.items():
		missing_tests = sorted(required - _test_names(paths[filename]))
		if missing_tests:
			raise SystemExit(f"Required behavioral tests are missing from {filename}: {missing_tests}")
	if not files or not tests:
		raise SystemExit("No Frappe test functions were collected from the AOS source tree.")
	print(f"Frappe collection assertion: {tests} test methods/functions in {files} files.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
