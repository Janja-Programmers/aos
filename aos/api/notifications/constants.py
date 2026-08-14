"""Notification API constants and stable category aliases."""

from aos.services.notifications.contracts import (
    CATEGORY_ACCOUNT,
    CATEGORY_ACTIVITY,
    CATEGORY_ALL,
    CATEGORY_COMMUNICATION,
    CATEGORY_MARKETPLACE,
    CATEGORY_TYPES,
    NOTIFICATION_TYPES,
    VALID_CATEGORIES,
)

# Stable public aliases retained for existing imports/mobile clients.
NOTIFICATION_CATEGORY_ALL = CATEGORY_ALL
NOTIFICATION_CATEGORY_COMMUNICATION = CATEGORY_COMMUNICATION
NOTIFICATION_CATEGORY_ACTIVITY = CATEGORY_ACTIVITY
NOTIFICATION_CATEGORY_MARKETPLACE = CATEGORY_MARKETPLACE
NOTIFICATION_CATEGORY_ACCOUNT = CATEGORY_ACCOUNT
NOTIFICATION_CATEGORY_TYPES = CATEGORY_TYPES
VALID_NOTIFICATION_CATEGORIES = VALID_CATEGORIES

# Source-visible compatibility inventory. Runtime category ownership remains in
# aos.services.notifications.contracts; this set exists so established static
# compatibility audits can detect category removal/renaming without importing
# Frappe application code. The equality check makes drift fail closed.
_STATIC_COMPAT_NOTIFICATION_TYPES = frozenset(
    {
        "message",
        "missed_call",
        "follow",
        "new_short",
        "short_like",
        "short_comment",
        "short_mention",
        "comment_reply",
        "live_started",
        "ad_approved",
        "ad_rejected",
        "ad_expired",
        "review_received",
        "review_approved",
        "review_rejected",
        "verification_approved",
        "verification_rejected",
    }
)
if _STATIC_COMPAT_NOTIFICATION_TYPES != NOTIFICATION_TYPES:
    raise RuntimeError("Notification compatibility inventory is out of sync with canonical contracts.")

# Pagination
NOTIFICATION_DEFAULT_LIMIT = 20
NOTIFICATION_MAX_LIMIT = 50

# Rate limits
GET_PUSH_CONFIG_LIMIT_PER_MINUTE_PER_USER = 30
REGISTER_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER = 30
DEACTIVATE_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER = 30
LIST_NOTIFICATIONS_LIMIT_PER_MINUTE_PER_USER = 60
MARK_NOTIFICATION_READ_LIMIT_PER_MINUTE_PER_USER = 60
MARK_ALL_NOTIFICATIONS_READ_LIMIT_PER_MINUTE_PER_USER = 30
DELETE_NOTIFICATION_LIMIT_PER_MINUTE_PER_USER = 60
CLEAR_NOTIFICATIONS_LIMIT_PER_MINUTE_PER_USER = 10
