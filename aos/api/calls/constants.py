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
- incoming call push config is intentionally short-lived and high-priority
  because calls are time-sensitive and should not arrive stale.
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
GET_CALL_STATUS_LIMIT_PER_MINUTE_PER_USER = 120

# Incoming call push configuration

# Android FCM delivery priority.
# Firebase Admin SDK AndroidConfig.priority accepts: "high" or "normal".
INCOMING_CALL_FCM_PRIORITY = "high"

# Incoming calls should expire quickly. If the phone receives the push late,
# Flutter should not revive an old call.
INCOMING_CALL_FCM_TTL_SECONDS = 30

# Must match the Android notification channel created in Flutter:
# AndroidNotificationConfig.calls = AndroidNotificationChannel("aos_calls", ...)
INCOMING_CALL_ANDROID_CHANNEL_ID = "aos_calls"

# Firebase Admin SDK AndroidNotification.priority commonly accepts:
# "min", "low", "default", "high", "max".
INCOMING_CALL_ANDROID_NOTIFICATION_PRIORITY = "max"
