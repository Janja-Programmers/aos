"""
Constants for Call endpoints.

Rate limit constants are per minute per user.

Design notes:
- initiate_call is lower because it creates calls, sends realtime events,
  notifications, system messages, timeout jobs, and LiveKit tokens.
- accept/reject/cancel/end are normal call actions.
- mark_call_ringing can be called by the receiver UI when incoming call
  screen is shown, so it can be a little higher but still bounded.
- request_video_upgrade is used during an ongoing audio call to ask the
  other participant to switch to video.
- respond_video_upgrade is used by the other participant to accept or decline
  a pending video upgrade request.
- get_call_token is higher because reconnect/retry flows may request it
  more often.
- list_calls returns grouped call-history summaries by default.
- get_call_group_details returns the individual call records inside one
  grouped call-history row.
- delete_call_logs hides selected call logs for the current user only.
- clear_call_history hides all visible call logs for the current user only.
- get_call_status is used by Flutter to validate call state before restoring
  incoming call UI from background/terminated push payloads.
- CALL_TIMEOUT_SECONDS is used by initiate_call to pass the timeout delay
  to the queued timeout job.
- CALL_TIMEOUT_JOB_PATH is the dotted path to the background timeout task.
- CALL_TIMEOUT_JOB_QUEUE controls which Frappe queue handles timeout jobs.
- CALL_TIMEOUT_JOB_EXTRA_BUFFER_SECONDS gives the timeout job enough worker
  time to sleep and still complete safely before the queue timeout.
"""

# Call lifecycle rate limits
INITIATE_CALL_LIMIT_PER_MINUTE_PER_USER = 10
INCOMING_CALL_LIMIT_PER_MINUTE_PER_TARGET = 20

MARK_RINGING_LIMIT_PER_MINUTE_PER_USER = 120

ACCEPT_CALL_LIMIT_PER_MINUTE_PER_USER = 60
REJECT_CALL_LIMIT_PER_MINUTE_PER_USER = 60
CANCEL_CALL_LIMIT_PER_MINUTE_PER_USER = 60
END_CALL_LIMIT_PER_MINUTE_PER_USER = 60


# Video upgrade rate limits
REQUEST_VIDEO_UPGRADE_LIMIT_PER_MINUTE_PER_USER = 30
RESPOND_VIDEO_UPGRADE_LIMIT_PER_MINUTE_PER_USER = 60


# LiveKit token rate limits
GET_TOKEN_LIMIT_PER_MINUTE_PER_USER = 120


# History rate limits
LIST_CALLS_LIMIT_PER_MINUTE_PER_USER = 120
GET_CALL_GROUP_DETAILS_LIMIT_PER_MINUTE_PER_USER = 120
DELETE_CALL_LOGS_LIMIT_PER_MINUTE_PER_USER = 60
CLEAR_CALL_HISTORY_LIMIT_PER_MINUTE_PER_USER = 20


# Status/recovery rate limits
GET_CALL_STATUS_LIMIT_PER_MINUTE_PER_USER = 120


# Missed call timeout enqueue constants
CALL_TIMEOUT_SECONDS = 30
CALL_TIMEOUT_JOB_PATH = "aos.tasks.calls.handle_call_timeout"
CALL_TIMEOUT_JOB_QUEUE = "short"
CALL_TIMEOUT_JOB_EXTRA_BUFFER_SECONDS = 60
