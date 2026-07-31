# Interactions

Likes, saves, active reposts, report reviews, sound links, daily viewers, daily aggregates and processing generations are protected by database uniqueness after migration reconciliation.

Toggle operations are idempotent and counters are bounded at zero. Impression/share/download events use SHA-256 event keys derived from event type, Short, actor and client `event_id` (or a short server time bucket). Replays do not increment counters again.

Views store the maximum watch time per actor/day. Watch time is capped at stored duration plus a small delivery tolerance. Qualification remains based on the established minimum-time/percentage rules.

Deleting a top-level comment soft-deletes its active replies and decrements the Short counter by the exact number removed. Comment creation and direct event/view writes recheck canonical policy below the HTTP layer.
