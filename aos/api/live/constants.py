"""
Rate-limit and business constants for Live Stream endpoints.

Designed for:
- Social/user-owned live streams
- Guest watching
- Logged-in interactions
- High concurrency
- Burst interactions such as reactions and comments
- Co-host invitation and request workflows
- Abuse prevention without hurting UX
"""


# LIFECYCLE

# Starting a live is relatively rare.
START_LIVE_LIMIT_PER_MINUTE_PER_USER = 5

# Joining a live and generating the initial participant session.
JOIN_LIVE_LIMIT_PER_MINUTE_PER_USER = 30
JOIN_LIVE_LIMIT_PER_MINUTE_PER_IP = 240

# Ending a live.
END_LIVE_LIMIT_PER_MINUTE_PER_USER = 20

# Sharing an active Live into a native AOS Chat message.
SHARE_LIVE_TO_CHAT_LIMIT_PER_MINUTE_PER_USER = 30

# Fetching live details and live feeds.
GET_LIVE_LIMIT_PER_MINUTE_PER_IP = 120
LIST_LIVE_STREAMS_LIMIT_PER_MINUTE_PER_IP = 120


# TOKEN

# LiveKit token generation and refresh.
GET_LIVE_TOKEN_LIMIT_PER_MINUTE_PER_USER = 60


# TRACKING

# Guest and authenticated viewer tracking.
TRACK_JOIN_LIMIT_PER_MINUTE_PER_IP = 120
TRACK_LEAVE_LIMIT_PER_MINUTE_PER_IP = 120


# LIVE MESSAGES / COMMENTS

# Live chat requires authentication.
# Per-Live sender budgets keep rapid interaction in one room from poisoning the
# same user's next Live session. A higher cross-room guard still bounds abuse.
ADD_COMMENT_LIMIT_PER_MINUTE_PER_USER = 60
REPLY_COMMENT_LIMIT_PER_MINUTE_PER_USER = 60
ADD_COMMENT_GLOBAL_LIMIT_PER_MINUTE_PER_USER = 240
REPLY_COMMENT_GLOBAL_LIMIT_PER_MINUTE_PER_USER = 240
# Shared room fan-out budget across comments + replies. Durable chat must apply
# backpressure before a viral room overwhelms websocket fan-out.
LIVE_COMMENT_FANOUT_LIMIT_PER_MINUTE = 1200

# Message and reply listing can be guest-accessible.
LIST_COMMENTS_LIMIT_PER_MINUTE_PER_IDENTITY = 120
LIST_REPLIES_LIMIT_PER_MINUTE_PER_IDENTITY = 120
# Cross-room guard. Per-room keys prevent a healthy viewer from carrying a
# depleted read bucket from Live A into Live B, while this higher global cap
# still bounds abusive fan-out across many Live IDs from one client IP.
LIST_COMMENTS_GLOBAL_LIMIT_PER_MINUTE_PER_IDENTITY = 600
LIST_REPLIES_GLOBAL_LIMIT_PER_MINUTE_PER_IDENTITY = 600

# Public comment soft deletion requires authentication.
DELETE_COMMENT_LIMIT_PER_MINUTE_PER_USER = 30


# REACTIONS

# Reactions require authentication but should support rapid interaction.
SEND_REACTION_LIMIT_PER_MINUTE_PER_USER = 600


# CO-HOST WORKFLOW

# Host invitations to active viewers.
INVITE_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER = 10

# Viewer requests to become a co-host.
REQUEST_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER = 5

# Accepting or rejecting invitations and requests.
RESPOND_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER = 10

# Cancelling pending invitations or requests.
CANCEL_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER = 10

# Activating an accepted co-host after LiveKit connection.
ACTIVATE_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER = 10

# Voluntary co-host leave or host removal.
END_LIVE_COHOST_LIMIT_PER_MINUTE_PER_USER = 10

# Fetching current or pending co-host state.
GET_LIVE_COHOST_LIMIT_PER_MINUTE_PER_IP = 120
LIST_LIVE_COHOSTS_LIMIT_PER_MINUTE_PER_IP = 120

# Co-host LiveKit token generation or refresh.
GET_LIVE_COHOST_TOKEN_LIMIT_PER_MINUTE_PER_USER = 30


# BUSINESS LOGIC CONSTANTS

# Minimum number of watch seconds required for a qualified view.
LIVE_VIEW_QUALIFICATION_SECONDS = 5

# Pending host invitations and viewer requests expire after this duration.
LIVE_COHOST_REQUEST_EXPIRY_SECONDS = 60

# Initial implementation supports one accepted or active co-host per live.
LIVE_COHOST_MAX_ACTIVE_SLOTS = 1
