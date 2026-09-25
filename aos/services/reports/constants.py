"""Canonical constants for the AOS human Reports domain."""

from __future__ import annotations

STATUS_REVIEWING = "Reviewing"
STATUS_RESOLVED = "Resolved"
STATUS_REJECTED = "Rejected"
REPORT_STATUSES = frozenset({STATUS_REVIEWING, STATUS_RESOLVED, STATUS_REJECTED})
REPORT_TRANSITIONS = {
    STATUS_REVIEWING: frozenset({STATUS_RESOLVED, STATUS_REJECTED}),
    STATUS_RESOLVED: frozenset(),
    STATUS_REJECTED: frozenset(),
}

REPORT_TARGET_USER = "User"
REPORT_TARGET_AD = "Ad"
REPORT_TARGET_SHORT = "Short"
# Reviews is already a hardened feature and owns Review-report submission. It
# consumes the shared reason master only so its existing contract does not
# regress when Report Reason classification becomes mandatory.
REPORT_TARGET_REVIEW = "Review"
REPORT_REASON_TARGETS = frozenset(
    {REPORT_TARGET_USER, REPORT_TARGET_AD, REPORT_TARGET_SHORT, REPORT_TARGET_REVIEW}
)
PUBLIC_REPORT_TARGETS = {
    "user": REPORT_TARGET_USER,
    "ad": REPORT_TARGET_AD,
    "short": REPORT_TARGET_SHORT,
}

TRANSPORT_FIELDS = frozenset({"cmd"})

SUBMISSION_FIELDS = {
    "AOS User Report": ("reported_user", "reported_by", "reason", "details"),
    "AOS Ad Report": ("ad", "reported_by", "seller", "reason", "details"),
    "AOS Short Report": ("short", "short_owner", "reported_by", "reason", "details"),
    "AOS Review Report": ("review", "reported_by", "reason", "details"),
}

DETAIL_MAX_LENGTH = {
    "AOS User Report": 1000,
    "AOS Ad Report": 1000,
    "AOS Short Report": 1000,
    "AOS Review Report": 500,
}

USER_REPORT_FIELDS = frozenset({"account_id", "reason_id", "details"})
SHORT_REPORT_FIELDS = frozenset({"short_id", "reason_id", "details"})
AD_REPORT_FIELDS = frozenset({"ad_id", "reason_id", "details"})
REASONS_FIELDS = frozenset({"target_type"})

REPORT_REASONS_LIMIT_PER_MINUTE_PER_USER = 60
REPORT_SUBMISSION_LIMIT_PER_MINUTE_PER_USER = 10
REPORT_SUBMISSION_LIMIT_PER_MINUTE_PER_TARGET = 3
