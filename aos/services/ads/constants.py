"""Bounded Ads domain constants."""

from __future__ import annotations

AD_DOCTYPE = "AOS Ad"
AD_DRAFT_DOCTYPE = "AOS Ad Draft"
AD_IMAGE_DOCTYPE = "AOS Ad Image"
AD_ATTRIBUTE_VALUE_DOCTYPE = "AOS Ad Attribute Value"
WISHLIST_DOCTYPE = "AOS Wishlist"
AD_REPORT_DOCTYPE = "AOS Ad Report"

STATUS_REVIEWING = "Reviewing"
STATUS_ACTIVE = "Active"
STATUS_DECLINED = "Declined"
STATUS_SOLD = "Sold"
STATUS_EXPIRED = "Expired"
STATUS_DELETED = "Deleted"
STATUS_SUSPENDED = "Suspended"

AD_STATUSES = frozenset(
    {
        STATUS_REVIEWING,
        STATUS_ACTIVE,
        STATUS_DECLINED,
        STATUS_SOLD,
        STATUS_EXPIRED,
        STATUS_DELETED,
        STATUS_SUSPENDED,
    }
)
PUBLIC_AD_STATUSES = frozenset({STATUS_ACTIVE})
TERMINAL_AD_STATUSES = frozenset({STATUS_DELETED, STATUS_SUSPENDED})
SELLER_EDITABLE_FULL_STATUSES = frozenset({STATUS_REVIEWING, STATUS_DECLINED})
SELLER_EDITABLE_SAFE_STATUSES = frozenset({STATUS_ACTIVE})
SELLER_BLOCKED_EDIT_STATUSES = frozenset({STATUS_SOLD, STATUS_EXPIRED, STATUS_DELETED, STATUS_SUSPENDED})

ACTION_MARK_SOLD = "mark_sold"
ACTION_MARK_AVAILABLE = "mark_available"
ACTION_RENEW = "renew"
ACTION_DELETE = "delete"
SELLER_ACTIONS = frozenset({ACTION_MARK_SOLD, ACTION_MARK_AVAILABLE, ACTION_RENEW, ACTION_DELETE})

MAX_IMAGES = 4
MAX_AD_ATTRIBUTES = 100
MAX_TITLE_LENGTH = 140
MIN_TITLE_LENGTH = 5
MAX_DESCRIPTION_LENGTH = 5_000
MIN_DESCRIPTION_LENGTH = 20
MAX_PRICE_UNIT_LENGTH = 80
MAX_SEARCH_QUERY_LENGTH = 160
MAX_REPORT_DETAILS_LENGTH = 2_000
MAX_DRAFT_PAYLOAD_BYTES = 64 * 1024
MAX_DRAFT_STEP = 20
MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 20
MAX_OFFSET = 5_000
MAX_SORT_ORDER = 1_000_000
MAX_ATTRIBUTE_TEXT_LENGTH = 500
MAX_ATTRIBUTE_TEXTAREA_LENGTH = 5_000
MAX_ATTRIBUTE_JSON_LENGTH = 8_000
MONEY_DECIMAL_PLACES = 6
MONEY_MAX_DIGITS = 21

ALLOWED_PRICE_TYPES = frozenset({"Fixed", "Negotiable", "Contact for price"})
PRICE_TYPES_REQUIRING_AMOUNT = frozenset({"Fixed", "Negotiable"})
PRICE_TYPES_WITHOUT_AMOUNT = frozenset({"Contact for price"})

PUBLIC_LIST_SORTS = frozenset({"rating_high", "price_low", "price_high", "recent"})
PUBLIC_PROMOTION_TYPES = frozenset({"offer", "deal", "flash_sale"})

CREATE_FIELDS = frozenset(
    {
        "title",
        "location",
        "category",
        "description",
        "details",
        "images",
        "price_type",
        "price",
        "price_unit",
        "offer_price",
        "offer_start_date",
        "offer_end_date",
        "video_media",
        "idempotency_key",
    }
)
FULL_UPDATE_FIELDS = (CREATE_FIELDS - {"idempotency_key"}) | frozenset({"ad_id", "version"})
ACTIVE_UPDATE_FIELDS = frozenset(
    {
        "ad_id",
        "version",
        "title",
        "description",
        "price_type",
        "price",
        "price_unit",
        "offer_price",
        "offer_start_date",
        "offer_end_date",
    }
)
STATUS_FIELDS = frozenset({"ad_id", "action", "version"})
GET_AD_FIELDS = frozenset({"ad_id", "currency", "country"})
GET_MY_AD_FIELDS = frozenset({"ad_id"})
LIST_MY_AD_FIELDS = frozenset({"limit", "offset", "status"})
PUBLIC_LIST_FIELDS = frozenset(
    {
        "country",
        "currency",
        "location",
        "category",
        "seller",
        "attributes",
        "q",
        "price_type",
        "promotion_type",
        "price_min",
        "price_max",
        "rating_min",
        "verified_seller",
        "sort",
        "limit",
        "offset",
        "cursor",
    }
)
WISHLIST_TOGGLE_FIELDS = frozenset({"ad_id", "id", "wishlisted"})
WISHLIST_LIST_FIELDS = PUBLIC_LIST_FIELDS
REPORT_AD_FIELDS = frozenset({"ad", "ad_id", "reason", "details"})
DRAFT_UPSERT_FIELDS = frozenset({"draft_id", "payload", "last_step", "version"})
DRAFT_ID_FIELDS = frozenset({"draft_id", "version"})
DRAFT_LIST_FIELDS = frozenset({"limit", "offset"})
