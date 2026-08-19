"""Bounded Catalog domain constants."""

CATEGORY_NAME_MAX_LENGTH = 120
CATEGORY_ID_MAX_LENGTH = 140
ATTRIBUTE_LABEL_MAX_LENGTH = 120
ATTRIBUTE_UNIT_MAX_LENGTH = 32
ATTRIBUTE_HELP_TEXT_MAX_LENGTH = 500
OPTION_MAX_LENGTH = 120
MAX_OPTIONS = 100
MAX_ATTRIBUTE_OPTIONS = 1500
MAX_CATEGORY_ATTRIBUTES = 500
MAX_CATEGORIES = 1000
MAX_CATEGORY_DEPTH = 2
MAX_SORT_ORDER = 1_000_000

ALLOWED_ATTRIBUTE_TYPES = frozenset(
    {"Text", "Number", "Select", "Boolean", "Date", "Year", "Textarea", "MultiSelect"}
)
ALLOWED_PRICING_REQUIREMENTS = frozenset({"Required", "Optional", "Hidden"})
ALLOWED_PRICE_TYPES = frozenset({"Fixed", "Negotiable", "Contact for price", "Free"})
SELECT_ATTRIBUTE_TYPES = frozenset({"Select", "MultiSelect"})
