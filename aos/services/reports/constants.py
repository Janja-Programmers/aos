"""Canonical constants for the existing AOS Report domain."""

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

MODERATOR_ROLES = frozenset({"System Manager", "AOS Moderator"})
TRANSPORT_FIELDS = frozenset({"cmd"})

REPORT_ACTIONS = {
    "AOS User Report": frozenset({"", "Warn User", "Suspend User", "Dismiss Report"}),
    "AOS Ad Report": frozenset({"", "Warn Seller", "Suspended Ad", "Suspended Seller"}),
    "AOS Short Report": frozenset({"", "Hide Short", "Warn Creator", "Suspend Creator", "Dismiss Report"}),
    "AOS Review Report": frozenset({""}),
}

SUBMISSION_FIELDS = {
    "AOS User Report": ("reported_user", "reported_by", "reason", "details"),
    "AOS Ad Report": ("ad", "reported_by", "seller", "reason", "details"),
    "AOS Short Report": ("short", "short_owner", "reported_by", "reason", "details"),
    "AOS Review Report": ("review", "reported_by", "reason", "details"),
}

DETAIL_MAX_LENGTH = {
    "AOS User Report": 1000,
    "AOS Ad Report": 2000,
    "AOS Short Report": 1000,
    "AOS Review Report": 500,
}

USER_REPORT_FIELDS = frozenset({"target_user", "user", "reason", "details", "block_user", "also_block"})
SHORT_REPORT_FIELDS = frozenset({"short", "short_id", "reason", "details"})
AD_REPORT_FIELDS = frozenset({"ad", "ad_id", "reason", "details"})
REASONS_FIELDS = frozenset()

REPORT_REASONS_LIMIT_PER_MINUTE_PER_USER = 120
REPORT_AD_LIMIT_PER_MINUTE_PER_USER = 10
REPORT_USER_LIMIT_PER_MINUTE_PER_USER = 10
REPORT_SHORT_LIMIT_PER_MINUTE_PER_USER = 10
