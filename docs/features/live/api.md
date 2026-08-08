# Live API Contracts

## Envelope

Frappe returns the helper result inside its normal `message` property. The domain envelope is:

```json
{"ok": true, "message": "...", "data": {}}
```

or:

```json
{"ok": false, "message": "...", "error": "LIVE_*", "data": {}}
```

Clients must branch on `error`, not human-readable `message` text. Correct HTTP status codes are set on the Frappe response.

## Stable endpoints

Base form: `/api/method/aos.api.v1.live.<endpoint>`.

| Endpoint | HTTP | Access | Accepted domain fields |
|---|---|---|---|
| `start_live` | POST | authenticated | `title`, `cover_image`, `live_cover_media` or compatible cover-media alias |
| `join_live` | POST | guest | `live_id`, `session_id` |
| `end_live` | POST | authenticated host | `live_id` |
| `get_live` | GET | guest | `live_id`, optional `session_id` |
| `list_live_streams` | GET | guest | optional `session_id`, `limit`, `start`, `cursor` |
| `get_live_token` | POST | authenticated | `live_id`, required viewer `session_id` except host |
| `get_live_cohost_token` | POST | accepted/active candidate | `cohost_id`, `session_id` |
| `track_join` | POST | guest | `live_id`, `session_id` |
| `track_leave` | POST | guest | `live_id`, `session_id` |
| `add_live_message` | POST | authenticated participant | `live_id`, `session_id`, `content`, optional `idempotency_key` |
| `reply_live_message` | POST | authenticated participant | `live_id`, `parent_message`, `session_id`, `content`, optional `idempotency_key` |
| `list_live_messages` | GET | guest | `live_id`, `limit`, `start`, `cursor` |
| `list_live_replies` | GET | guest | `parent_message`, `limit`, `start`, `cursor` |
| `delete_live_message` | POST | author or host | `message_id` |
| `send_reaction` | POST | authenticated participant | `live_id`, `reaction_type`, `session_id` |
| `invite_live_cohost` | POST | host | preferred: `live_id`, opaque `livekit_identity`; legacy: `live_id`, public `target_user`, `session_id` |
| `request_live_cohost` | POST | active viewer | `live_id`, `session_id` |
| `respond_live_cohost` | POST | intended responder | `cohost_id`, `action`, optional `reason` |
| `cancel_live_cohost` | POST | authorized workflow party | `cohost_id`, optional `reason` |
| `activate_live_cohost` | POST | accepted candidate | `cohost_id`, `session_id` |
| `end_live_cohost` | POST | host or active co-host | `cohost_id`, optional `reason` |
| `get_live_cohost` | GET | host or candidate | `cohost_id` |
| `list_live_cohosts` | GET | host/candidate | `live_id`, optional `status`, `limit`, `start`, `cursor` |

The signed webhook endpoint is `/api/method/aos.api.v1.livekit.handle_webhook` and is not a client endpoint.

## Input rules

- `cmd` is stripped as Frappe transport metadata. Other unknown fields fail with `LIVE_UNKNOWN_FIELD`.
- Conflicting aliases fail with `LIVE_ALIAS_CONFLICT`.
- Live IDs must match `LIVE-YYYY-NNNNN`.
- Account references accept canonical `ACC-*` and the established legacy compatibility form; serializers return public account IDs.
- `limit` is 1–100 at the public boundary; implementation-specific pages are further capped, generally at 50.
- `start` is 0–10000 and cannot be combined with `cursor`.
- Cursors are signed, endpoint-scoped, and limited to 2048 characters.
- Session IDs are at most 128 characters; titles 140; reasons 240; comments 500; comment idempotency keys 128.
- Host co-host invitations should use the opaque `aos:participant:*` LiveKit identity already visible in the host room. The backend resolves that identity to the active authenticated viewer and private AOS session; clients must not obtain or submit another viewer's session ID. The legacy `target_user + session_id` form remains accepted for compatibility.
- Text is NFC-normalized. NUL and whitespace-only comments are rejected. Comment content is HTML-escaped before persistence and broadcast.

## Stable Live errors

| Error | HTTP | Meaning |
|---|---:|---|
| `LIVE_INVALID_REQUEST` | 422 | Invalid scalar, action, or request shape |
| `LIVE_UNKNOWN_FIELD` | 422 | Unreviewed field supplied |
| `LIVE_ALIAS_CONFLICT` | 422 | Conflicting compatible aliases |
| `LIVE_INVALID_IDENTIFIER` | 422 | Malformed public ID |
| `LIVE_INPUT_TOO_LARGE` | 413 | Bounded text/cursor limit exceeded |
| `LIVE_INVALID_CURSOR` | 422 | Malformed, tampered, wrong-scope cursor |
| `LIVE_PAGINATION_CONFLICT` | 422 | Cursor and offset combined |
| `LIVE_ACCESS_DENIED` | 403 | Authenticated action not authorized |
| `LIVE_NOT_FOUND` | 404 | Missing or intentionally non-enumerable inaccessible Live |
| `LIVE_CONFLICT` | 409 | Concurrent/duplicate domain conflict |
| `LIVE_INVALID_STATE` | 409 | Lifecycle/workflow transition not permitted |
| `LIVE_COHOST_SLOT_UNAVAILABLE` | 409 | Existing accepted or active co-host occupies the slot |
| `LIVE_DEPENDENCY_UNAVAILABLE` | 503 | Required signing/config/dependency unavailable |
| `LIVE_WEBHOOK_INVALID` | 401 or 413 | Missing/invalid signature or oversized webhook body |
| `LIVE_WEBHOOK_RETRY` | 503 | Verified event could not be applied atomically; LiveKit should retry |
| `LIVE_INTERNAL_ERROR` | 500 | Sanitized unexpected failure |

Existing shared codes such as `AUTH_REQUIRED`, `RATE_LIMIT`, and media errors remain compatible.

## Pagination

New clients should use `next_cursor`. Legacy `start` remains supported but cannot be combined with a cursor. Ordering is deterministic:

- discovery: `started_at DESC, creation DESC, name DESC`;
- messages and replies: `creation ASC, name ASC`;
- co-host workflows: `creation DESC, name DESC`.

## Curl smoke examples

```bash
# Authenticated start (cookie/session omitted here)
curl -X POST "$SITE/api/method/aos.api.v1.live.start_live" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Staging Live"}'

# Public discovery
curl "$SITE/api/method/aos.api.v1.live.list_live_streams?limit=20"

# Public detail
curl "$SITE/api/method/aos.api.v1.live.get_live?live_id=LIVE-2026-00001"

# Host invites an authenticated viewer already present in the LiveKit room.
# The identity is the opaque RemoteParticipant.identity observed by the host.
curl -X POST "$SITE/api/method/aos.api.v1.live.invite_live_cohost" \
  -H 'Content-Type: application/json' \
  -d '{"live_id":"LIVE-2026-00001","livekit_identity":"aos:participant:abcdefghijklmnopqrstuvwx"}'
```

Do not place LiveKit tokens in URLs, logs, Postman examples, or shared test evidence.
