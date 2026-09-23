"""
Constants for Call endpoints.

Rate limit constants are per minute per user.

Design notes:
- initiate_call is lower because it creates calls, sends realtime events,
  durable setup state, notifications, system messages, and shared LiveKit provisioning.
- accept/reject/cancel/end are normal call actions.
- mark_call_ringing can be called by an invited participant UI when the incoming-call
  screen is shown, so it can be a little higher but still bounded.
- request_video_upgrade is used during an ongoing audio call to ask the
  other participant to switch to video.
- respond_video_upgrade is used by the other participant to accept or decline
  a pending video upgrade request.
- get_call_token is higher because reconnect/retry flows may request it
  more often.
- add_call_participants is bounded because each invite fans out realtime and push delivery.
- list_calls returns cursor-paginated call-history summaries with participant projections.
- delete_call_logs hides selected call logs for the current user only.
- clear_call_history hides all visible call logs for the current user only.
- get_call_status is used by Flutter to validate call state before restoring
  incoming call UI from background/terminated push payloads.
- CALL_RING_TIMEOUT_SECONDS is the durable unanswered-call window. The worker
  writes ring_expires_at when incoming delivery becomes visible to the invited participant.
"""

# Call lifecycle rate limits
INITIATE_CALL_LIMIT_PER_MINUTE_PER_USER = 10
INCOMING_CALL_LIMIT_PER_MINUTE_PER_TARGET = 20
CALL_INVITEE_FANOUT_LIMIT_PER_MINUTE_PER_USER = 120

MARK_RINGING_LIMIT_PER_MINUTE_PER_USER = 120

ACCEPT_CALL_LIMIT_PER_MINUTE_PER_USER = 60
REJECT_CALL_LIMIT_PER_MINUTE_PER_USER = 60
CANCEL_CALL_LIMIT_PER_MINUTE_PER_USER = 60
END_CALL_LIMIT_PER_MINUTE_PER_USER = 60
ADD_CALL_PARTICIPANTS_LIMIT_PER_MINUTE_PER_USER = 30


# Video upgrade rate limits
REQUEST_VIDEO_UPGRADE_LIMIT_PER_MINUTE_PER_USER = 30
RESPOND_VIDEO_UPGRADE_LIMIT_PER_MINUTE_PER_USER = 60


# LiveKit token rate limits
GET_TOKEN_LIMIT_PER_MINUTE_PER_USER = 120


# History rate limits
LIST_CALLS_LIMIT_PER_MINUTE_PER_USER = 120
DELETE_CALL_LOGS_LIMIT_PER_MINUTE_PER_USER = 60
CLEAR_CALL_HISTORY_LIMIT_PER_MINUTE_PER_USER = 20


# Status/recovery rate limits
GET_CALL_STATUS_LIMIT_PER_MINUTE_PER_USER = 120


# Missed call timeout. The minute scheduler scans indexed durable state;
# Calls do not consume one sleeping background worker per ring.
CALL_RING_TIMEOUT_SECONDS = 30
