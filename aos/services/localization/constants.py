"""Localization domain constants and bounded resource limits."""

LOCALIZATION_CACHE_SCHEMA = "v4"
LOCALIZATION_CACHE_TTL_SECONDS = 900
LOCALIZATION_DEFAULTS_CACHE_KEY = "aos:localization:defaults:v4"
LOCALIZATION_BUNDLE_CACHE_KEY = "aos:localization:bundle:v4"
LOCALIZATION_SCHEMA_VERSION = "2.0"

MAX_COUNTRY_INPUT_LENGTH = 140
MAX_CURRENCY_INPUT_LENGTH = 16
MAX_LANGUAGE_INPUT_LENGTH = 140
MAX_ACCEPT_LANGUAGE_LENGTH = 512
MAX_ACCEPT_LANGUAGE_ITEMS = 20

# These reference tables are inherently small, but public bootstrap must remain
# bounded even if an administrator imports malformed/duplicated master data.
MAX_COUNTRIES = 300
MAX_CURRENCIES = 300
MAX_LANGUAGES = 1000
