"""Canonical Reviews policy constants."""

from __future__ import annotations

REVIEW_DOCTYPE = "AOS Review"
REACTION_DOCTYPE = "AOS Review Reaction"
REPORT_DOCTYPE = "AOS Review Report"
REVIEW_IMAGE_DOCTYPE = "AOS Review Image"

STATUS_PENDING = "Pending"
STATUS_APPROVED = "Approved"
STATUS_REJECTED = "Rejected"
STATUS_HIDDEN = "Hidden"
STATUS_WITHDRAWN = "Withdrawn"
PUBLIC_STATUSES = frozenset({STATUS_APPROVED})
TERMINAL_STATUSES = frozenset({STATUS_REJECTED, STATUS_WITHDRAWN})
ALL_STATUSES = frozenset(
    {STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_HIDDEN, STATUS_WITHDRAWN}
)

RATING_MIN = 1
RATING_MAX = 5
RATING_STEP = 1
TITLE_MIN_LENGTH = 2
TITLE_MAX_LENGTH = 120
COMMENT_MIN_LENGTH = 2
COMMENT_MAX_LENGTH = 2000
REPORT_DETAILS_MAX_LENGTH = 500
MAX_REVIEW_IMAGES = 5

ELIGIBILITY_BASIS_COMMUNICATION = "communication"

PUBLIC_SORTS = {
    "newest": "creation desc, name desc",
    "helpful": "like_count desc, creation desc, name desc",
    "rating_high": "rating desc, creation desc, name desc",
    "rating_low": "rating asc, creation desc, name desc",
}
SELF_SORTS = {
    "newest": "creation desc, name desc",
    "oldest": "creation asc, name asc",
    "rating_high": "rating desc, creation desc, name desc",
    "rating_low": "rating asc, creation desc, name desc",
}

REACTIONS = frozenset({"Like", "Dislike"})

DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 50

RATE_LIMITS = {
    "viewer_state": 120,
    "create": 6,
    "update": 12,
    "delete": 8,
    "get": 120,
    "list_public": 120,
    "list_private": 90,
    "reaction": 60,
    "report": 8,
}
