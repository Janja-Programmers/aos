"""Accounts/Profile constants.

Central place for profile editing rules.
"""

# Expand cautiously. If you later allow changing email/mobile, implement
# a verification flow instead of updating them directly.
EDITABLE_USER_FIELDS = {
    "full_name",
    "user_image",
    "bio",
}

FULL_NAME_MIN_LEN = 2
FULL_NAME_MAX_LEN = 80

BIO_MAX_LEN = 300

# Common Frappe file_url patterns.
# If you later use full CDN URLs, you can relax this to accept
# https://... but keep the File existence check in validators.
FILE_URL_ALLOWED_PREFIXES = (
    "/files/",
    "/private/files/",
)

# Rate limits (defense-in-depth).
GET_PROFILE_LIMIT_PER_MINUTE_PER_USER = 60
UPDATE_PROFILE_LIMIT_PER_MINUTE_PER_USER = 20
GET_PREF_LIMIT_PER_MINUTE_PER_USER = 60
UPDATE_PREF_LIMIT_PER_MINUTE_PER_USER = 20