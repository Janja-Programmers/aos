"""Canonical Activity Center taxonomy and operating bounds.

Activity is a private user-owned read model. Only events with a real producer
in the current repository are represented here. Producer domains remain
canonical for resource state and authorization.
"""

from __future__ import annotations

ACTIVITY_DOCTYPE = "AOS User Activity"

ACTIVE_STATUS = "Active"
HIDDEN_STATUS = "Hidden"
CLEARED_STATUS = "Cleared"
VALID_ACTIVITY_STATUSES = frozenset({ACTIVE_STATUS, HIDDEN_STATUS, CLEARED_STATUS})

EVENT_MODE_COALESCE = "coalesce"
EVENT_MODE_ONCE = "once"

# Metadata types are intentionally small and scalar. Activity is not an event
# bus or analytics payload store. Required keys are producer-contract fields;
# every other key listed is an optional presentation snapshot.
EVENT_SPECS: dict[str, dict[str, object]] = {
    "ad_view": {
        "group": "Ads", "mode": EVENT_MODE_COALESCE, "route_type": "ad", "target_doctype": "AOS Ad",
        "metadata": {"seller": "seller_id", "category": "text", "location": "text", "country": "text", "ad_status": "text", "price_type": "text", "currency": "text", "price": "number"},
        "required_metadata": frozenset(),
    },
    "ad_wishlist": {
        "group": "Ads", "mode": EVENT_MODE_COALESCE, "route_type": "ad", "target_doctype": "AOS Ad",
        "metadata": {"seller": "seller_id", "category": "text", "location": "text", "country": "text", "ad_status": "text", "price_type": "text", "currency": "text", "price": "number"},
        "required_metadata": frozenset(),
    },
    "ad_posted": {
        "group": "Ads", "mode": EVENT_MODE_ONCE, "route_type": "ad", "target_doctype": "AOS Ad",
        "metadata": {"seller": "seller_id", "category": "text", "location": "text", "country": "text", "ad_status": "text", "price_type": "text", "currency": "text", "price": "number"},
        "required_metadata": frozenset(),
    },
    "ad_report": {
        "group": "Ads", "mode": EVENT_MODE_ONCE, "route_type": "ad", "target_doctype": "AOS Ad",
        "metadata": {"reason": "text"}, "required_metadata": frozenset(),
    },
    "short_report": {
        "group": "Shorts", "mode": EVENT_MODE_ONCE, "route_type": "short", "target_doctype": "AOS Short",
        "metadata": {"reason": "text"}, "required_metadata": frozenset(),
    },
    "user_search": {
        "group": "Search", "mode": EVENT_MODE_COALESCE, "route_type": "user_search", "target_doctype": "",
        "metadata": {"query": "text", "result_count": "int"}, "required_metadata": frozenset({"query"}),
    },
    "user_follow": {
        "group": "Social", "mode": EVENT_MODE_COALESCE, "route_type": "profile", "target_doctype": "User",
        "metadata": {"target_user": "account_id"}, "required_metadata": frozenset({"target_user"}),
    },
    "user_block": {
        "group": "Social", "mode": EVENT_MODE_COALESCE, "route_type": "profile", "target_doctype": "User",
        "metadata": {"target_user": "account_id", "reason": "text"}, "required_metadata": frozenset({"target_user"}),
    },
    "user_report": {
        "group": "Social", "mode": EVENT_MODE_ONCE, "route_type": "profile", "target_doctype": "User",
        "metadata": {"target_user": "account_id", "reason": "text"}, "required_metadata": frozenset({"target_user"}),
    },
    "live_host": {
        "group": "Live", "mode": EVENT_MODE_ONCE, "route_type": "live", "target_doctype": "AOS Live Stream",
        "metadata": {"live_id": "text", "host_user": "account_id"}, "required_metadata": frozenset({"live_id", "host_user"}),
    },
    "live_join": {
        "group": "Live", "mode": EVENT_MODE_COALESCE, "route_type": "live", "target_doctype": "AOS Live Stream",
        "metadata": {"live_id": "text", "host_user": "account_id"}, "required_metadata": frozenset({"live_id", "host_user"}),
    },
    "live_comment": {
        "group": "Live", "mode": EVENT_MODE_ONCE, "route_type": "live", "target_doctype": "AOS Live Message",
        "metadata": {"live_id": "text", "host_user": "account_id", "is_reply": "bool", "comment_preview": "text"},
        "required_metadata": frozenset({"live_id", "host_user", "is_reply"}),
    },
}

VALID_ACTIVITY_TYPES = frozenset(EVENT_SPECS)
VALID_ACTIVITY_GROUPS = frozenset(str(spec["group"]) for spec in EVENT_SPECS.values())

PUBLIC_TARGET_KIND_BY_ROUTE = {"ad": "ad", "short": "short", "profile": "profile", "live": "live", "user_search": "search"}

ACTIVITY_PUBLIC_ID_PREFIX = "ACT"
ACTIVITY_PUBLIC_ID_MAX_LEN = 36
ACTIVITY_GROUP_MAX_LEN = 40
ACTIVITY_TYPE_MAX_LEN = 80
TARGET_TEXT_MAX_LEN = 140
TARGET_SUBTITLE_MAX_LEN = 500
TARGET_IMAGE_MAX_LEN = 500
ROUTE_TYPE_MAX_LEN = 80
ROUTE_ID_MAX_LEN = 140
UNIQUE_KEY_MAX_LEN = 128
METADATA_JSON_MAX_BYTES = 8 * 1024
METADATA_MAX_KEYS = 16
METADATA_TEXT_MAX_LEN = 500
ACTIVITY_COUNT_MAX = 2_147_483_647

DEFAULT_ACTIVITY_LIMIT = 20
MAX_ACTIVITY_LIMIT = 50
MAX_CURSOR_LENGTH = 1024
CLEAR_BATCH_SIZE = 500
RETENTION_DELETE_BATCH_SIZE = 5_000
ACTIVITY_RETENTION_DAYS = 180

LIST_ACTIVITY_LIMIT_PER_MINUTE_PER_USER = 120
HIDE_ACTIVITY_LIMIT_PER_MINUTE_PER_USER = 60
CLEAR_ACTIVITY_LIMIT_PER_MINUTE_PER_USER = 20

LIST_FIELDS = frozenset({"limit", "cursor", "group", "type"})
HIDE_FIELDS = frozenset({"activity_id"})
CLEAR_FIELDS = frozenset({"group", "type"})
