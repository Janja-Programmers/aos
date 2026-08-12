"""Canonical Activity Center constants.

Only activity groups/types already produced by the repository belong here.
"""

from __future__ import annotations

ACTIVITY_DOCTYPE = "AOS User Activity"

ACTIVE_STATUS = "Active"
HIDDEN_STATUS = "Hidden"
CLEARED_STATUS = "Cleared"
VALID_ACTIVITY_STATUSES = frozenset({ACTIVE_STATUS, HIDDEN_STATUS, CLEARED_STATUS})

VALID_ACTIVITY_GROUPS = frozenset(
    {
        "Shorts",
        "Ads",
        "Search",
        "Social",
        "Live",
        "Reviews",
        "Account",
        "Other",
    }
)

# Only types that are actually emitted by the current backend are public API
# filter values. Reviews/Account remain reserved schema groups with no producer.
VALID_ACTIVITY_TYPES = frozenset(
    {
        "ad_view",
        "ad_wishlist",
        "ad_posted",
        "ad_report",
        "short_watch",
        "short_like",
        "short_comment",
        "short_repost",
        "short_report",
        "user_search",
        "user_follow",
        "user_block",
        "user_report",
        "live_host",
        "live_join",
        "live_comment",
    }
)

PUBLIC_TARGET_KIND_BY_ROUTE = {
    "ad": "ad",
    "short": "short",
    "profile": "profile",
    "live": "live",
    "user_search": "search",
}

# Storage/API bounds. Frappe Data fields default to 140 characters unless a
# source-controlled length explicitly says otherwise.
ACTIVITY_ID_MAX_LEN = 140
ACTIVITY_GROUP_MAX_LEN = 40
ACTIVITY_TYPE_MAX_LEN = 80
TARGET_TEXT_MAX_LEN = 140
TARGET_SUBTITLE_MAX_LEN = 500
TARGET_IMAGE_MAX_LEN = 500
ROUTE_TYPE_MAX_LEN = 80
ROUTE_ID_MAX_LEN = 140
UNIQUE_KEY_MAX_LEN = 128
METADATA_JSON_MAX_BYTES = 8 * 1024

DEFAULT_ACTIVITY_LIMIT = 20
MAX_ACTIVITY_LIMIT = 50
MAX_ACTIVITY_START = 100_000
CLEAR_BATCH_SIZE = 500

LIST_ACTIVITY_LIMIT_PER_MINUTE_PER_USER = 120
HIDE_ACTIVITY_LIMIT_PER_MINUTE_PER_USER = 60
CLEAR_ACTIVITY_LIMIT_PER_MINUTE_PER_USER = 20

LIST_FIELDS = frozenset({"limit", "start", "group", "activity_group", "type", "activity_type"})
HIDE_FIELDS = frozenset({"activity_id", "id"})
CLEAR_FIELDS = frozenset({"group", "activity_group", "type", "activity_type"})
