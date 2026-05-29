"""
Rate limit constants for Chat endpoints.

All *_PER_MINUTE_* limits are per minute.

Design notes:
- Conversation/message endpoints are normal user actions.
- Status and typing endpoints are intentionally higher because they can be
  triggered frequently by foreground chat screens.
- Presence broadcasts are throttled separately to avoid realtime spam.
"""

# Conversation

# Opening/reusing conversations from:
# - ad detail
# - seller storefront
# - user profile
# - followers/following
OPEN_CONVERSATION_LIMIT_PER_MINUTE_PER_USER = 60

# Chat inbox refresh/pagination.
LIST_CONVERSATIONS_LIMIT_PER_MINUTE_PER_USER = 120

# Soft-delete/hide conversation.
DELETE_CONVERSATION_LIMIT_PER_MINUTE_PER_USER = 60


# Message

# Sending text/media/ad messages.
SEND_MESSAGE_LIMIT_PER_MINUTE_PER_USER = 120

# Forwarding one message to one or more conversations.
FORWARD_MESSAGE_LIMIT_PER_MINUTE_PER_USER = 60

# Editing sent messages.
EDIT_MESSAGE_LIMIT_PER_MINUTE_PER_USER = 30

# Delete one or more messages for me/everyone.
DELETE_MESSAGES_LIMIT_PER_MINUTE_PER_USER = 60

# Clear all visible messages in a conversation for the current user.
CLEAR_CHAT_LIMIT_PER_MINUTE_PER_USER = 20

# Star/unstar messages for the current user.
TOGGLE_MESSAGE_STAR_LIMIT_PER_MINUTE_PER_USER = 120

# Listing starred messages for the current user.
LIST_STARRED_MESSAGES_LIMIT_PER_MINUTE_PER_USER = 120

# Add/change/remove message reactions.
TOGGLE_MESSAGE_REACTION_LIMIT_PER_MINUTE_PER_USER = 120

# Message pagination can happen more often while scrolling.
LIST_MESSAGES_LIMIT_PER_MINUTE_PER_USER = 300


# Status

# High-frequency endpoints.
# These may run when:
# - chat screen opens
# - app resumes
# - realtime message arrives
# - conversation becomes visible
MARK_DELIVERED_LIMIT_PER_MINUTE_PER_USER = 600
MARK_READ_LIMIT_PER_MINUTE_PER_USER = 600

# Realtime / Presence
# Typing events are frequent but lightweight.
# Frontend should still debounce typing calls.
SEND_TYPING_LIMIT_PER_MINUTE_PER_USER = 600

# Prevent presence events from being broadcast too frequently.
PRESENCE_BROADCAST_THROTTLE_SECONDS = 10

# User is considered online if last_active is within this window.
ONLINE_THRESHOLD_SECONDS = 60
