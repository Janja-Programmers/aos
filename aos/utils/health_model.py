"""Canonical operational health primitives for AOS Diagnostics.

The model deliberately separates dependency requirement from observed state.
An optional dependency can be unhealthy without making the whole application
traffic-unready, while a required dependency in an unhealthy/unknown/disabled
state blocks readiness for the report that owns that check.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

HEALTHY = "healthy"
DEGRADED = "degraded"
UNHEALTHY = "unhealthy"
DISABLED = "disabled"
UNKNOWN = "unknown"

HEALTH_STATUSES = frozenset({HEALTHY, DEGRADED, UNHEALTHY, DISABLED, UNKNOWN})
REQUIRED = "required"
OPTIONAL = "optional"
DEPENDENCY_REQUIREMENTS = frozenset({REQUIRED, OPTIONAL})

AVAILABLE = "available"
UNAVAILABLE = "unavailable"
MISCONFIGURED = "misconfigured"
TIMED_OUT = "timeout"
DISABLED_CONDITION = "disabled"
UNKNOWN_CONDITION = "unknown"
DEPENDENCY_CONDITIONS = frozenset(
	{AVAILABLE, UNAVAILABLE, MISCONFIGURED, TIMED_OUT, DISABLED_CONDITION, UNKNOWN_CONDITION}
)

_BLOCKING_REQUIRED_STATUSES = frozenset({UNHEALTHY, UNKNOWN, DISABLED})


def _clean(value: Any) -> str:
	return str(value or "").strip()


def health_check(
	*,
	name: str,
	category: str,
	status: str,
	requirement: str,
	message: str,
	condition: str = AVAILABLE,
	details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
	"""Build one deterministic, JSON-safe health-check record."""

	clean_status = _clean(status).lower()
	if clean_status not in HEALTH_STATUSES:
		clean_status = UNKNOWN
	clean_requirement = _clean(requirement).lower()
	if clean_requirement not in DEPENDENCY_REQUIREMENTS:
		clean_requirement = REQUIRED
	clean_condition = _clean(condition).lower()
	if clean_condition not in DEPENDENCY_CONDITIONS:
		clean_condition = UNKNOWN_CONDITION
	return {
		"name": _clean(name),
		"category": _clean(category),
		"requirement": clean_requirement,
		"status": clean_status,
		"condition": clean_condition,
		"message": _clean(message),
		"details": dict(details or {}),
	}


def summarize_health(checks: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
	"""Summarize checks without treating optional failures as readiness blockers."""

	rows = [dict(check) for check in checks]
	counts = {status: 0 for status in (HEALTHY, DEGRADED, UNHEALTHY, DISABLED, UNKNOWN)}
	required = {"checks": 0, "blocking": 0}
	optional = {"checks": 0, "impaired": 0, "disabled": 0}

	for check in rows:
		status = _clean(check.get("status")).lower()
		if status not in counts:
			status = UNKNOWN
		counts[status] += 1
		requirement = _clean(check.get("requirement")).lower()
		if requirement == OPTIONAL:
			optional["checks"] += 1
			if status == DISABLED:
				optional["disabled"] += 1
			elif status != HEALTHY:
				optional["impaired"] += 1
		else:
			required["checks"] += 1
			if status in _BLOCKING_REQUIRED_STATUSES:
				required["blocking"] += 1

	ready = required["blocking"] == 0
	if not ready:
		overall = UNHEALTHY
	elif counts[DEGRADED] or counts[UNKNOWN] or optional["impaired"]:
		overall = DEGRADED
	else:
		overall = HEALTHY

	return {
		"status": overall,
		"ready": ready,
		"summary": {
			"checks": len(rows),
			**counts,
			"required": required,
			"optional": optional,
		},
		"checks": rows,
	}
