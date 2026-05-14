"""
Rate limit and business constants for Live Stream endpoints.

Designed for:
- Social/user-owned live streams
- Guest watching
- Logged-in interactions
- High concurrency
- Burst interactions such as reactions/comments
- Abuse prevention without hurting UX
"""


# LIFECYCLE

# Starting live is rare.
START_LIVE_LIMIT_PER_MINUTE_PER_USER = 5

# Joining live interactively / token setup.
JOIN_LIVE_LIMIT_PER_MINUTE_PER_USER = 30

# Ending live.
END_LIVE_LIMIT_PER_MINUTE_PER_USER = 20

# Fetching live details / feeds.
GET_LIVE_LIMIT_PER_MINUTE_PER_IP = 120
LIST_LIVE_STREAMS_LIMIT_PER_MINUTE_PER_IP = 120


# TOKEN

# Token generation should be controlled.
GET_LIVE_TOKEN_LIMIT_PER_MINUTE_PER_USER = 60


# TRACKING

# Guest and logged-in viewer tracking.
TRACK_JOIN_LIMIT_PER_MINUTE_PER_IP = 120
TRACK_LEAVE_LIMIT_PER_MINUTE_PER_IP = 120


# COMMENTS

# Live chat requires login.
ADD_COMMENT_LIMIT_PER_MINUTE_PER_USER = 60
REPLY_COMMENT_LIMIT_PER_MINUTE_PER_USER = 60

# Fetch comments/replies can be guest-accessible.
LIST_COMMENTS_LIMIT_PER_MINUTE_PER_IP = 120
LIST_REPLIES_LIMIT_PER_MINUTE_PER_IP = 120

# Delete comment requires login.
DELETE_COMMENT_LIMIT_PER_MINUTE_PER_USER = 30


# REACTIONS

# Reactions require login, but allow fast taps.
SEND_REACTION_LIMIT_PER_MINUTE_PER_USER = 300


# BUSINESS LOGIC CONSTANTS

# Minimum seconds to count as a qualified view.
LIVE_VIEW_QUALIFICATION_SECONDS = 5
