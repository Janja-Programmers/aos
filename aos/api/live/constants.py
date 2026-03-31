"""
Rate limit constants for Live Stream endpoints.

Designed for:
- High concurrency (live streams)
- Burst interactions (reactions, comments)
- Abuse prevention without hurting UX
"""


# LIFECYCLE

# Starting live is rare
START_LIVE_LIMIT_PER_MINUTE_PER_USER = 5

# Joining live (token + setup)
JOIN_LIVE_LIMIT_PER_MINUTE_PER_USER = 30

# Ending live
END_LIVE_LIMIT_PER_MINUTE_PER_USER = 20

# Fetching live details / feeds
GET_LIVE_LIMIT_PER_MINUTE_PER_IP = 120
LIST_LIVE_STREAMS_LIMIT_PER_MINUTE_PER_IP = 120


# TOKEN

# Token generation (should be controlled)
GET_LIVE_TOKEN_LIMIT_PER_MINUTE_PER_USER = 60


# TRACKING (VERY FREQUENT)

# Join tracking (viewer enters)
TRACK_JOIN_LIMIT_PER_MINUTE_PER_IP = 120

# Leave tracking
TRACK_LEAVE_LIMIT_PER_MINUTE_PER_IP = 120


# COMMENTS

# Sending messages in live chat
ADD_COMMENT_LIMIT_PER_MINUTE_PER_USER = 60

# Replying
REPLY_COMMENT_LIMIT_PER_MINUTE_PER_USER = 60

# Fetch comments
LIST_COMMENTS_LIMIT_PER_MINUTE_PER_IP = 120
LIST_REPLIES_LIMIT_PER_MINUTE_PER_IP = 120

# Delete comment
DELETE_COMMENT_LIMIT_PER_MINUTE_PER_USER = 30


# REACTIONS (HIGH BURST)

# Allow rapid taps but still prevent spam bots
SEND_REACTION_LIMIT_PER_MINUTE_PER_IP = 300


# LIVE ADS
ATTACH_AD_LIMIT_PER_MINUTE_PER_USER = 30
REMOVE_AD_LIMIT_PER_MINUTE_PER_USER = 30
PIN_AD_LIMIT_PER_MINUTE_PER_USER = 60
LIST_LIVE_ADS_LIMIT_PER_MINUTE_PER_IP = 120


# BUSINESS LOGIC CONSTANTS

# Minimum seconds to count as a qualified view
LIVE_VIEW_QUALIFICATION_SECONDS = 5

# Optional: max ads per live (UI/business constraint)
MAX_ADS_PER_LIVE = 50