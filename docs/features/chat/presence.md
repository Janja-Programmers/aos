# Chat presence and typing

Presence is intentionally coarse and privacy-aware.

- User activity updates persisted `last_active` in the caller-managed transaction.
- Presence broadcasts are scheduled after commit.
- A user is considered online within the configured 60-second window.
- Peer-wide presence broadcasts are throttled to at most once per 10 seconds per user.
- Only active conversation peers are eligible subscribers.
- Bidirectional Social blocks suppress direct and peer-broadcast presence.
- Presence payloads use public account/display identity helpers; they must not expose raw Frappe User identifiers.

`send_typing_event` is transient and rate-limited at 600/minute/user. Clients should debounce and auto-clear typing after a short timeout if a false event is missed.
