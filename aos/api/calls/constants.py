"""
Constants for Call endpoints.

Rate limit constants are per minute per user.

Design notes:
- initiate_call is lower because it creates calls, sends realtime events,
  notifications, system messages, and LiveKit tokens.
- accept/reject/cancel/end are normal call actions.
- mark_call_ringing can be called by the receiver UI when incoming call
  screen is shown, so it can be a little higher but still bounded.
- get_call_token is higher because reconnect/retry flows may request it
  more often.
- get_call_status is used by Flutter to validate call state before restoring
  incoming call UI from background/terminated push payloads.
"""

# Call lifecycle rate limits
INITIATE_CALL_LIMIT_PER_MINUTE_PER_USER = 30

MARK_RINGING_LIMIT_PER_MINUTE_PER_USER = 120

ACCEPT_CALL_LIMIT_PER_MINUTE_PER_USER = 60
REJECT_CALL_LIMIT_PER_MINUTE_PER_USER = 60
CANCEL_CALL_LIMIT_PER_MINUTE_PER_USER = 60
END_CALL_LIMIT_PER_MINUTE_PER_USER = 60


# LiveKit token rate limits
GET_TOKEN_LIMIT_PER_MINUTE_PER_USER = 120


# History rate limits
LIST_CALLS_LIMIT_PER_MINUTE_PER_USER = 120


# Status/recovery rate limits
GET_CALL_STATUS_LIMIT_PER_MINUTE_PER_USER = 120
