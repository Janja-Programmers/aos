from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

DURABLE_SERVICES = {
	"video-processing": ("aos/services/video_processing_service.py", "dispatch_video_processing_job"),
	"moderation": ("aos/services/moderation_service.py", "dispatch_moderation_job"),
	"search-ranking": ("aos/services/search_ranking_service.py", "dispatch_search_index_job"),
	"notification-delivery": (
		"aos/services/notification_delivery_service.py",
		"dispatch_notification_delivery_job",
	),
	"analytics-pipeline": (
		"aos/services/analytics_pipeline_service.py",
		"dispatch_analytics_ingest_job",
	),
}
REQUIRED_COMPANION_ACTIONS = {
	"enqueued",
	"stale_generation_replaced",
	"duplicate_active",
	"callback_replay_scheduled",
	"callback_already_completed",
	"reconciliation_pending",
	"newer_generation_exists",
}
REQUIRED_LIFECYCLE_MARKERS = {
	"execute_work_job",
	"deliver_callback",
	"enqueue_or_reconcile",
	"replay_callback_delivery",
	"job_status",
	"resolve_uncertain_outcome",
	"authorize_work_replay",
	"RetryableWorkError",
	"TerminalWorkError",
	"_work_job_id",
	"_callback_job_id",
	"result_persisted",
	"callback_dead_lettered",
}
REQUIRED_FRAPPE_TESTS = {
	"aos/tests/test_outbox_recovery_dispatch_all_services.py": {
		"test_callback_timeout_redispatches_all_five_services_and_replay_completes",
		"test_processing_duplicate_guard_is_bypassed_only_by_private_recovery_context",
		"test_recovery_requires_an_explicit_accepted_http_outcome_for_every_service",
		"test_expired_publisher_lease_for_processing_job_redispatches_all_services",
		"test_callback_timeout_exhaustion_enters_manual_review_for_every_service_type",
	},
	"aos/tests/test_callback_atomicity_all_services.py": {
		"test_valid_success_and_duplicate_success_are_atomic_and_idempotent",
		"test_valid_failure_and_duplicate_failure_are_atomic_and_idempotent",
		"test_conflicting_late_wrong_token_old_generation_and_dead_letter_change_nothing",
		"test_service_job_save_and_outbox_save_failures_roll_back_every_service",
		"test_operator_work_replay_changes_generation_and_rejects_old_callbacks",
	},
	"aos/tests/test_outbox_backfill.py": {
		"test_each_doctype_backfills_retryable_states_and_skips_terminal_states",
		"test_mixed_all_service_patch_execution_is_idempotent_batched_and_rollback_safe",
	},
	"aos/tests/test_transactional_outbox_recovery.py": {
		"test_terminal_failure_callback_is_not_redispatched",
		"test_valid_callback_remains_eligible_while_publisher_holds_lease",
		"test_companion_generation_floor_advances_next_proposal",
		"test_reconciliation_exhaustion_moves_to_manual_review",
		"test_callback_already_completed_repairs_terminal_failure_outbox",
	},
	"aos/tests/test_outbox_final_lifecycle_patch.py": {
		"test_patch_terminalizes_failure_callback_and_is_idempotent",
		"test_patch_normalizes_legacy_claim_and_exhausted_reconciliation",
		"test_outbox_conflict_before_action_assignment_preserves_original_error",
		"test_system_manager_work_replay_reopens_same_job_and_outbox",
	},
}


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
	for node in ast.walk(tree):
		if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
			return node
	return None


def _call_names(node: ast.AST) -> set[str]:
	result: set[str] = set()
	for child in ast.walk(node):
		if not isinstance(child, ast.Call):
			continue
		function = child.func
		if isinstance(function, ast.Name):
			result.add(function.id)
		elif isinstance(function, ast.Attribute):
			parts: list[str] = []
			current: ast.expr = function
			while isinstance(current, ast.Attribute):
				parts.append(current.attr)
				current = current.value
			if isinstance(current, ast.Name):
				parts.append(current.id)
			result.add(".".join(reversed(parts)))
	return result


def _test_names(path: Path) -> set[str]:
	tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
	return {
		node.name
		for node in ast.walk(tree)
		if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
	}


def main() -> int:
	root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
	errors: list[str] = []
	services = [
		line.strip() for line in (root / "ci/service-matrix.txt").read_text().splitlines() if line.strip()
	]

	helper_hashes: dict[str, str] = {}
	for service in services:
		helper = root / "infra" / service / "app" / "validation_fields.py"
		observability = root / "infra" / service / "app" / "observability.py"
		if not helper.is_file():
			errors.append(f"missing validation-field helper: {helper.relative_to(root)}")
			continue
		helper_hashes[service] = hashlib.sha256(helper.read_bytes()).hexdigest()
		text = observability.read_text(encoding="utf-8") if observability.is_file() else ""
		required = (
			"from app.validation_fields import validation_fields",
			"RequestValidationError",
			"request_validation_error_handler",
			"app.add_exception_handler(RequestValidationError",
		)
		for marker in required:
			if marker not in text:
				errors.append(
					f"{observability.relative_to(root)} lacks validation-sanitization marker: {marker}"
				)
	if helper_hashes and len(set(helper_hashes.values())) != 1:
		errors.append(
			"companion validation-field helpers have diverged; update the synchronized shared implementation"
		)

	lifecycle_hashes: dict[str, str] = {}
	for service, (relative, function_name) in DURABLE_SERVICES.items():
		service_path = root / relative
		tree = ast.parse(service_path.read_text(encoding="utf-8"), filename=str(service_path))
		function = _function(tree, function_name)
		if function is None:
			errors.append(f"missing durable dispatcher {function_name} in {relative}")
			continue
		parameters = {argument.arg for argument in function.args.args + function.args.kwonlyargs}
		unsafe_parameters = parameters & {
			"recovery_dispatch",
			"dispatch_generation",
			"dispatch_token",
			"stable_dispatch_id",
		}
		if unsafe_parameters:
			errors.append(f"{function_name} exposes unsafe recovery parameters: {sorted(unsafe_parameters)}")
		calls = _call_names(function)
		for required_call in (
			"current_outbox_dispatch_context",
			"requests.post",
			"record_companion_dispatch_outcome",
		):
			if required_call not in calls:
				errors.append(f"{function_name} does not call {required_call}")

		companion = root / "infra" / service / "app" / "idempotent_dispatch.py"
		lifecycle = root / "infra" / service / "app" / "durable_lifecycle.py"
		real_redis_test = root / "infra" / service / "tests" / "test_real_redis_rq_lifecycle.py"
		if not companion.is_file():
			errors.append(f"missing durable companion idempotency helper: {companion.relative_to(root)}")
			continue
		if not lifecycle.is_file():
			errors.append(f"missing durable work/callback lifecycle: {lifecycle.relative_to(root)}")
			continue
		lifecycle_text = lifecycle.read_text(encoding="utf-8")
		lifecycle_hashes[service] = hashlib.sha256(lifecycle.read_bytes()).hexdigest()
		for action in REQUIRED_COMPANION_ACTIONS:
			if action not in lifecycle_text:
				errors.append(f"{lifecycle.relative_to(root)} lacks supported outcome {action}")
		for marker in REQUIRED_LIFECYCLE_MARKERS:
			if marker not in lifecycle_text:
				errors.append(f"{lifecycle.relative_to(root)} lacks durable-lifecycle marker {marker}")
		if "job_id=_work_job_id(service_type, stable_id)" not in lifecycle_text:
			errors.append(
				f"{lifecycle.relative_to(root)} does not preserve the stable downstream RQ work-job ID"
			)
		if "job_id=job_id" not in lifecycle_text or "_callback_job_id" not in lifecycle_text:
			errors.append(f"{lifecycle.relative_to(root)} lacks deterministic callback-delivery job IDs")
		real_test_text = real_redis_test.read_text(encoding="utf-8") if real_redis_test.is_file() else ""
		if not real_redis_test.is_file() or "@pytest.mark.integration" not in real_test_text:
			errors.append(
				f"missing real Redis/RQ lifecycle integration test: {real_redis_test.relative_to(root)}"
			)
		for marker in (
			"ScheduledJobRegistry",
			"registry.requeue",
			"test_real_redis_rq_retries_transient_work_before_terminal_result",
			'work_state"] == "retrying"',
			'callback_status"] == "complete"',
		):
			if marker not in real_test_text:
				errors.append(f"{real_redis_test.relative_to(root)} lacks scheduled retry evidence: {marker}")

	if lifecycle_hashes and len(set(lifecycle_hashes.values())) != 1:
		errors.append(
			"durable companion lifecycle modules have diverged; update all five synchronized copies"
		)

	workflow_text = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
	for marker in (
		"AOS_TEST_REDIS_URL: redis://127.0.0.1:16379/15",
		"redis:7.4.5-alpine@sha256:",
		"fastapi-unit-tests",
	):
		if marker not in workflow_text:
			errors.append(f"CI does not enforce real Redis/RQ lifecycle tests: missing {marker}")

	for relative, required_tests in REQUIRED_FRAPPE_TESTS.items():
		path = root / relative
		if not path.is_file():
			errors.append(f"missing behavioral Frappe test module: {relative}")
			continue
		missing = sorted(required_tests - _test_names(path))
		if missing:
			errors.append(f"{relative} lacks required behavioral tests: {missing}")

	if errors:
		print("Companion/outbox safety validation failed:", file=sys.stderr)
		for error in errors:
			print(f"- {error}", file=sys.stderr)
		return 1
	print(
		f"Validated synchronized 422 field sanitization for {len(services)} services, controlled recovery "
		f"dispatch for {len(DURABLE_SERVICES)} durable services, and required behavioral Frappe tests."
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
