"""Distributed abuse limits shared by report submission endpoints."""

from __future__ import annotations

from typing import Any

from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.services.reports.constants import (
    REPORT_SUBMISSION_LIMIT_PER_MINUTE_PER_TARGET,
    REPORT_SUBMISSION_LIMIT_PER_MINUTE_PER_USER,
)


def limit_report_submission(*, report_type: str, user: str, target_id: Any):
    global_limit = rate_limit(
        key=rate_limit_key("reports", report_type, "user", user),
        ttl_seconds=60,
        limit=REPORT_SUBMISSION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many reports. Please try again shortly.",
    )
    if global_limit:
        return global_limit
    return rate_limit(
        key=rate_limit_key("reports", report_type, "target", user, target_id),
        ttl_seconds=60,
        limit=REPORT_SUBMISSION_LIMIT_PER_MINUTE_PER_TARGET,
        message="Too many reports for this target. Please try again shortly.",
    )
