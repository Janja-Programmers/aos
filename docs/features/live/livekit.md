# LiveKit Integration

## Configuration

Required server-side environment/site configuration:

- LiveKit WebSocket endpoint (`ws://` or `wss://`);
- API key;
- API secret;
- Frappe site `encryption_key` for opaque participant identities and signed cursors.

Secrets are read only on the server. They are never returned in an API serializer or structured Live log.

`AOS Settings.livekit_live_token_ttl_minutes` controls Live token lifetime. Default: 15 minutes. Effective range: 1–30 minutes. The pre-existing call-token TTL remains independent.

## Self-hosted transport and scale

The checked-in LiveKit configuration uses UDP mux on `7882/udp` instead of the former 11-port range and a dedicated `livekit-redis` coordination service. This removes the small fixed UDP-range ceiling and makes the deployment topology ready for additional LiveKit nodes. `7881/tcp` remains the RTC fallback port and `7880` remains signaling/API behind the reverse proxy.

Additional LiveKit nodes increase aggregate room capacity; a single room still has to fit on one media node. Size CPU/network and test the largest intended room before production traffic.

## Server-generated rooms and identities

Room names are generated as `live:<LIVE-ID>` by the Live Stream controller and are immutable. Clients cannot supply room names.

Participant identities are HMAC-derived opaque values. Host identities are Live-scoped. Viewer and co-host identities are derived from the Live, public account/guest scope, and immutable session. Co-host promotion preserves the viewer participant identity to avoid duplicate participants.

Metadata is bounded JSON containing only public account ID, role, display name, avatar, guest flag, and the allowed co-host workflow reference. It contains no email, internal User name, session ID, token, or secret.

## Grants

| Role | Join | Subscribe | Publish media | Publish data |
|---|---:|---:|---:|---:|
| host | yes | yes | yes | yes |
| co-host | yes | yes | yes | yes |
| viewer | yes | yes | no | no |

The backend determines the role. A client-supplied role is not an accepted API field.

## Admin calls

Room creation, deletion, participant listing, and participant removal occur only in background tasks. Calls use:

- a five-second timeout;
- at most three attempts;
- bounded exponential backoff;
- categorized failure results rather than raw upstream exceptions;
- at most 2,000 participant identities per reconciliation response.

Room deletion and participant removal treat not-found as idempotent success.

## Webhook

Configure LiveKit to POST to:

```text
https://<site>/api/method/aos.api.v1.livekit.handle_webhook
```

The handler must receive the original raw body and LiveKit `Authorization` header. It verifies with `WebhookReceiver(TokenVerifier(...))`, rejects missing/invalid signatures, caps the body at 128 KiB, and stores only event ID/type/hash/status/timestamps/outcome—not the raw body.

Handled events:

- `room_started` (informational only);
- `room_finished` (terminal application reconciliation);
- `participant_joined` (touch authorized tracked presence);
- `participant_left`;
- `participant_connection_aborted`.

Unsupported signed events are acknowledged as ignored. Event IDs are unique and retained for 30 days after completion. Failed processing removes the dedupe row so LiveKit retry can recover.

## Demotion and removal

Cancelling an accepted co-host or ending an active co-host queues `RemoveParticipant` after commit. A blocked/unavailable tracked participant observed by webhook is closed locally and removed after commit. New token issuance rechecks current application role, account state, block state, Live state, and session.

For self-hosted LiveKit deployments that do not provide server-side token revocation beyond disconnection, an already-issued token can remain cryptographically valid until its short expiry. Application token issuance is still denied immediately, and removal is retried. This is the main residual integration risk to verify against the deployed LiveKit edition/version.

## Operational verification

After deployment:

1. Confirm server workers can import the pinned `livekit-api` package.
2. Confirm `ws(s)` endpoint converts to the corresponding `http(s)` admin endpoint.
3. Start a staging Live and verify room creation.
4. Verify viewer tokens cannot publish.
5. Promote and remove a co-host; verify disconnection and inability to obtain another co-host token.
6. End the Live; verify room deletion or pending cleanup followed by reconciliation.
7. Replay a captured signed webhook event ID; verify a duplicate success without counter drift.
