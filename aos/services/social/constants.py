"""Canonical Social constants and public contract."""

FOLLOW_DOCTYPE = "AOS Follow"
PROFILE_DOCTYPE = "AOS Profile"
USER_DOCTYPE = "User"
BLOCK_DOCTYPE = "AOS User Block"
NOTIFICATION_DOCTYPE = "AOS Notification"

BLOCK_ACTIVE = "Active"
BLOCK_UNBLOCKED = "Unblocked"

RELATIONSHIP_NONE = "none"
RELATIONSHIP_FOLLOWING = "following"
RELATIONSHIP_FOLLOWED_BY = "followed_by"
RELATIONSHIP_FRIENDS = "friends"

ACCOUNT_ACTIVE = "Active"

DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 50
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 50
SEARCH_MIN_LENGTH = 2
SEARCH_MAX_LENGTH = 80
BLOCK_REASON_MAX_LENGTH = 300
CURSOR_MAX_LENGTH = 700
FOLLOW_NOTIFICATION_DEDUPE_SECONDS = 300
MAX_SOCIAL_EVENT_FANOUT = 500

RATE_LIMITS = {
    "follow": 24,
    "unfollow": 36,
    "relationship": 180,
    "following": 90,
    "followers": 90,
    "friends": 90,
    "search": 40,
    "block": 12,
    "unblock": 18,
    "block_status": 120,
    "blocked_list": 90,
}

TARGET_FIELDS = frozenset({"account_id"})
FOLLOW_FIELDS = TARGET_FIELDS
BLOCK_FIELDS = frozenset({"account_id", "reason"})
RELATIONSHIP_FIELDS = TARGET_FIELDS
LIST_FIELDS = frozenset({"limit", "cursor", "search"})
SEARCH_FIELDS = frozenset({"query", "limit", "cursor"})
BLOCK_LIST_FIELDS = frozenset({"limit", "cursor"})
