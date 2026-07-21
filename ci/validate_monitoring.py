from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

REQUIRED_ALERTS = {
	"AOSApiErrorRateSpike",
	"AOSApiLatencyDegraded",
	"AOSServiceReadinessFailure",
	"AOSDatabaseReadinessFailure",
	"AOSRedisReadinessFailure",
	"AOSBackgroundQueueBacklog",
	"AOSOldestQueuedJobTooOld",
	"AOSStaleOutboxLeases",
	"AOSDeadLetterJobsPresent",
	"AOSRepeatedWorkerFailures",
	"AOSBackupOverdue",
	"AOSBackupVerificationFailed",
	"AOSBackupEncryptionDisabled",
	"AOSOffsiteBackupOverdue",
	"AOSRestoreRehearsalStale",
	"AOSDiskSpaceRisk",
	"AOSProductionConfigurationUnhealthy",
	"AOSPublishedCallbackOverdue",
	"AOSFrappeMetricsTargetDown",
	"AOSBackgroundMetricsTargetDown",
	"AOSRedisExporterDown",
	"AOSDatabaseExporterDown",
	"AOSMinIOMetricsTargetDown",
	"AOSWorkerServiceMetricsTargetDown",
	"AOSBackupCollectorDown",
	"AOSCriticalOperationalMetricsAbsent",
	"AOSMetricsBackendUnavailable",
	"AOSMetricsBackendReadinessAbsent",
	"AOSCallbackRedispatchFailures",
	"AOSCompanionCallbackPendingTooLong",
	"AOSCompanionCallbackDeadLetters",
	"AOSDispatchUncertaintyUnresolved",
	"AOSRepeatedReconciliationFailures",
	"AOSCompanionHeartbeatStale",
	"AOSNotificationDedupeAnomaly",
	"AOSAnalyticsDuplicateEventAttempts",
	"AOSCompanionLifecycleMetricsAbsent",
	"AOSOutboxManualReviewRequired",
	"AOSCallbackCompleteStateRepair",
	"AOSCompanionWorkRetryExhaustion",
	"AOSCallbackRetrySchedulerStalled",
	"AOSNotificationOutcomeUncertainty",
	"AOSDurableLifecycleIndexCleanupElevated",
}
REQUIRED_SCRAPE_JOBS = {
	"aos-frappe",
	"aos-background-jobs",
	"aos-backup-readiness",
	"aos-redis-exporter",
	"aos-database-exporter",
	"aos-minio",
	"aos-worker-services",
}
DOWN_ALERT_JOB_MATCHES = {
	"aos-frappe",
	"aos-background-jobs",
	"aos-backup-readiness",
	"aos-redis-exporter",
	"aos-database-exporter",
	"aos-minio",
	"aos-worker-services",
}
PLACEHOLDER_MARKERS = ("example.invalid", "CHANGE_ME", "REPLACE_ME", "<", "${")
APPROVED_PROMQL_LABELS = {
	"category",
	"dependency",
	"environment",
	"event",
	"job",
	"le",
	"method",
	"policy",
	"service",
	"service_type",
	"state",
	"status_class",
	"surface",
}
HIGH_CARDINALITY_LABELS = {
	"document_id",
	"email",
	"exception",
	"job_id",
	"job_name",
	"name",
	"outbox",
	"outbox_name",
	"path",
	"request_body",
	"token",
	"url",
	"user",
	"user_id",
}

REQUIRED_WORKER_TARGETS = {
	"127.0.0.1:8100": "translation",
	"127.0.0.1:8110": "image_search",
	"127.0.0.1:8120": "background_removal",
	"127.0.0.1:8130": "video_processing",
	"127.0.0.1:8140": "moderation",
	"127.0.0.1:8150": "search_ranking",
	"127.0.0.1:8160": "notification_delivery",
	"127.0.0.1:8170": "analytics_pipeline",
}


def _targets(config: dict[str, Any]) -> list[str]:
	targets: list[str] = []
	for manager in (config.get("alerting") or {}).get("alertmanagers") or []:
		for static in manager.get("static_configs") or []:
			targets.extend(str(value) for value in static.get("targets") or [])
	return targets


def _selector_label_names(expression: str) -> set[str]:
	names: set[str] = set()
	for selector in re.findall(r"\{([^{}]*)\}", expression):
		for match in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:=~|!~|!=|=)", selector):
			names.add(match.group(1))
	return names


def main() -> int:
	root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
	rules_path = root / "infra/monitoring/prometheus/alerts.yml"
	prometheus_path = root / "infra/monitoring/prometheus/prometheus.yml.example"
	alertmanager_path = root / "infra/monitoring/alertmanager/alertmanager.yml.example"
	rules = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
	prometheus = yaml.safe_load(prometheus_path.read_text(encoding="utf-8")) or {}
	alertmanager = yaml.safe_load(alertmanager_path.read_text(encoding="utf-8")) or {}
	errors: list[str] = []
	groups = rules.get("groups") or []
	names: set[str] = set()
	expressions: dict[str, str] = {}

	for group in groups:
		for rule in group.get("rules") or []:
			name = rule.get("alert")
			if not name or name in names:
				errors.append(f"invalid duplicate alert: {name}")
			if name:
				names.add(name)
				expressions[name] = str(rule.get("expr") or "")
			expression = str(rule.get("expr") or "")
			if not expression.strip():
				errors.append(f"missing expr: {name}")
			selector_labels = _selector_label_names(expression)
			forbidden_labels = sorted(selector_labels & HIGH_CARDINALITY_LABELS)
			unknown_labels = sorted(selector_labels - APPROVED_PROMQL_LABELS)
			if forbidden_labels:
				errors.append(f"high-cardinality PromQL labels in {name}: {forbidden_labels}")
			if unknown_labels:
				errors.append(f"unreviewed PromQL labels in {name}: {unknown_labels}")
			labels = rule.get("labels") or {}
			if labels.get("severity") not in {"warning", "critical", "info"}:
				errors.append(f"missing severity: {name}")
			annotations = rule.get("annotations") or {}
			if not annotations.get("description") or not annotations.get("runbook_url"):
				errors.append(f"missing actionable annotations: {name}")

	missing = sorted(REQUIRED_ALERTS - names)
	if missing:
		errors.append("missing alerts: " + ", ".join(missing))

	scrape_configs = prometheus.get("scrape_configs") or []
	scrape_jobs = {str(item.get("job_name")) for item in scrape_configs if item.get("job_name")}
	missing_jobs = sorted(REQUIRED_SCRAPE_JOBS - scrape_jobs)
	if missing_jobs:
		errors.append("missing scrape jobs: " + ", ".join(missing_jobs))

	worker_config = next(
		(item for item in scrape_configs if item.get("job_name") == "aos-worker-services"), {}
	)
	worker_targets: dict[str, str] = {}
	for static in worker_config.get("static_configs") or []:
		service = str((static.get("labels") or {}).get("service") or "")
		for target in static.get("targets") or []:
			worker_targets[str(target)] = service
	if worker_targets != REQUIRED_WORKER_TARGETS:
		errors.append("worker-service scrape targets do not match the private companion-service port map")

	rule_files = [str(item) for item in prometheus.get("rule_files") or []]
	if not any("alerts" in item for item in rule_files):
		errors.append("Prometheus configuration does not load AOS alert rules")

	targets = _targets(prometheus)
	if not targets:
		errors.append("Prometheus has no Alertmanager target")
	if any(re.search(r"https?://", target) for target in targets):
		errors.append("Alertmanager targets must be host:port values, not credential-bearing URLs")

	combined_down_expr = "\n".join(
		expression
		for name, expression in expressions.items()
		if name.endswith("TargetDown") or name.endswith("ExporterDown") or name.endswith("CollectorDown")
	)
	for job in sorted(DOWN_ALERT_JOB_MATCHES):
		if job not in combined_down_expr or "up" not in combined_down_expr:
			errors.append(f"missing up==0 coverage for scrape job: {job}")
	absent_expr = expressions.get("AOSCriticalOperationalMetricsAbsent", "")
	if "absent_over_time" not in absent_expr and "absent(" not in absent_expr:
		errors.append("critical metric absence alert must use absent() or absent_over_time()")
	metrics_backend_expr = expressions.get("AOSMetricsBackendUnavailable", "")
	if "aos_metrics_backend_ready" not in metrics_backend_expr or "< 1" not in metrics_backend_expr:
		errors.append("metrics-backend unavailable alert must test aos_metrics_backend_ready < 1")
	metrics_backend_absent = expressions.get("AOSMetricsBackendReadinessAbsent", "")
	if (
		"aos_metrics_backend_ready" not in metrics_backend_absent
		or "absent_over_time" not in metrics_backend_absent
	):
		errors.append("metrics-backend absence alert must use absent_over_time(aos_metrics_backend_ready)")
	redispatch_expr = expressions.get("AOSCallbackRedispatchFailures", "")
	if (
		"redispatch_failure" not in redispatch_expr
		or "aos_background_outbox_events_total" not in redispatch_expr
	):
		errors.append("callback redispatch failure alert must use the bounded outbox event metric")

	lifecycle_absent = expressions.get("AOSCompanionLifecycleMetricsAbsent", "")
	if (
		"aos_companion_lifecycle_metrics_ready" not in lifecycle_absent
		or "absent_over_time" not in lifecycle_absent
	):
		errors.append("companion lifecycle absence alert must use absent_over_time lifecycle readiness")
	callback_pending = expressions.get("AOSCompanionCallbackPendingTooLong", "")
	if (
		"aos_companion_callback_pending" not in callback_pending
		or "aos_companion_callback_oldest_age_seconds" not in callback_pending
	):
		errors.append("callback-pending alert must include pending count and oldest age")
	callback_dead = expressions.get("AOSCompanionCallbackDeadLetters", "")
	if "aos_companion_callback_jobs_dead_lettered_total" not in callback_dead:
		errors.append("callback dead-letter alert must use the companion dead-letter metric")
	manual_review = expressions.get("AOSOutboxManualReviewRequired", "")
	if "aos_background_manual_review_jobs" not in manual_review:
		errors.append("manual-review alert must use the bounded outbox manual-review gauge")
	repair = expressions.get("AOSCallbackCompleteStateRepair", "")
	if "automatic_outbox_repair" not in repair or "aos_background_outbox_events_total" not in repair:
		errors.append("callback-complete repair alert must use the bounded automatic-repair event")
	work_exhaustion = expressions.get("AOSCompanionWorkRetryExhaustion", "")
	if "aos_companion_work_retry_exhausted_total" not in work_exhaustion:
		errors.append("work retry exhaustion alert must use the companion exhaustion metric")
	scheduler_stalled = expressions.get("AOSCallbackRetrySchedulerStalled", "")
	if (
		"aos_companion_callback_jobs_retried_total" not in scheduler_stalled
		or "aos_companion_callback_oldest_age_seconds" not in scheduler_stalled
	):
		errors.append("callback retry scheduler alert must combine retry progress and callback age")
	notification_uncertain = expressions.get("AOSNotificationOutcomeUncertainty", "")
	if (
		"aos_companion_dispatch_uncertain_total" not in notification_uncertain
		or 'service_type="notification_delivery"' not in notification_uncertain
	):
		errors.append("notification uncertainty alert must use the bounded notification service metric")
	stale_cleanup = expressions.get("AOSDurableLifecycleIndexCleanupElevated", "")
	if "aos_companion_stale_index_cleanup_total" not in stale_cleanup:
		errors.append("durable lifecycle index cleanup alert must use the companion cleanup metric")

	receivers = alertmanager.get("receivers") or []
	if not receivers:
		errors.append("Alertmanager has no receivers")
	route = alertmanager.get("route") or {}
	if not route.get("receiver"):
		errors.append("Alertmanager route has no default receiver")

	alertmanager_text = alertmanager_path.read_text(encoding="utf-8")
	if re.search(
		r"https://(hooks\.slack\.com|discord\.com/api/webhooks|events\.pagerduty\.com|api\.pagerduty\.com)/",
		alertmanager_text,
	):
		errors.append("real-looking webhook URL committed")
	for key in ("password", "password_file", "bearer_token", "bearer_token_file", "api_key", "routing_key"):
		pattern = rf"(?im)^\s*{re.escape(key)}\s*:\s*([^#\n]+)"
		for match in re.finditer(pattern, alertmanager_text):
			value = match.group(1).strip().strip("'\"")
			if value and not any(marker in value for marker in PLACEHOLDER_MARKERS):
				errors.append(f"Alertmanager contains non-placeholder {key}")

	for unit_name, expected_state in (
		("aos-prometheus.service", "StateDirectory=aos-prometheus"),
		("aos-alertmanager.service", "StateDirectory=aos-alertmanager"),
	):
		unit_path = root / "infra/systemd" / unit_name
		if not unit_path.is_file():
			errors.append(f"missing monitoring systemd unit: {unit_name}")
			continue
		unit_text = unit_path.read_text(encoding="utf-8")
		if "127.0.0.1" not in unit_text:
			errors.append(f"{unit_name} is not private by default")
		if expected_state not in unit_text or "Restart=on-failure" not in unit_text:
			errors.append(f"{unit_name} lacks persistent state or restart policy")
		if "ExecStartPost=" not in unit_text:
			errors.append(f"{unit_name} lacks readiness health check")
		if "MemoryMax=" not in unit_text or "CPUQuota=" not in unit_text:
			errors.append(f"{unit_name} lacks resource limits")

	if errors:
		print("\n".join(errors), file=sys.stderr)
		return 1
	print(
		f"Validated {len(names)} Prometheus alerts, {len(scrape_jobs)} scrape jobs, "
		f"{len(targets)} Alertmanager target(s), and private monitoring units."
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
