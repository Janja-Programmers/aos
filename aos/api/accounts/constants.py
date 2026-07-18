"""Accounts/Profile constants.

Central place for profile editing rules.
"""

# Expand cautiously. If you later allow changing email/mobile, implement
# a verification flow instead of updating them directly.
EDITABLE_USER_FIELDS = {
    "full_name",
    "user_image",
    "user_image_media",
    "profile_image_media",
    "media_id",
    "bio",
}

FULL_NAME_MIN_LEN = 2
FULL_NAME_MAX_LEN = 80

BIO_MAX_LEN = 300

# Rate limits (defense-in-depth).
GET_PROFILE_LIMIT_PER_MINUTE_PER_USER = 60
UPDATE_PROFILE_LIMIT_PER_MINUTE_PER_USER = 20
GET_PREF_LIMIT_PER_MINUTE_PER_USER = 60
UPDATE_PREF_LIMIT_PER_MINUTE_PER_USER = 20
