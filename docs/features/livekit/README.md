# LiveKit integration

## Overview
Shared RTC infrastructure for Live and Calls. It owns provider-facing primitives, not call or stream business lifecycles.

## Responsibilities
Server-signed room/participant grants, room provisioning/deletion, bounded admin requests, verified webhook handling, participant reconciliation, and multi-node LiveKit/TURN configuration.

## Boundaries
`aos/services/livekit/`, `aos/services/livekit_service.py` and `aos/api/v1/livekit/` serve Live and Calls. The owners decide whether a canonical user may join; LiveKit room existence does not grant application membership. Accounts supplies opaque account projection; Social supplies block/privacy policy. Do not create RTC token issuers in product features.

## Architecture
Shared provider configuration separates public websocket and private administrative URLs. Provider webhooks are authenticated before state mutation. The Live feature owns durable webhook event records and applies ordered event replay guards. Room I/O occurs outside domain row locks followed by database revalidation.

## Data Model
`AOS LiveKit Webhook Event` records provider event identity and callback status. Live and Calls maintain their own durable room lifecycle; they do not share mutable product-state rows.

## Fields
Room name, grants, participant identity, TTL and capabilities are server-controlled; client payloads cannot choose them.

## API
`aos.api.v1.livekit.handle_webhook` is a signed provider callback; no client room-admin API exists. Live session endpoints live in [Live](../live/README.md); call tokens and room membership live in [Calls](../calls/README.md).

## Cross-feature Dependencies
Authentication, Accounts, Social, Live, Calls and Notifications; Frappe stores product authority, and LiveKit transports RTC media/signaling.

## Transaction / Concurrency Model
Webhook replay keys and monotonic event timestamps protect cross-worker order. Failures and ambiguous provider responses are recovered through bounded room reconciliation rather than assuming that an HTTP timeout means room absence. Product-side notifications/realtime require committed state.

## Security
Secrets are runtime configuration, not payload fields or logs. Grants are least-privilege and expire; signed webhook verification, payload limits and replay detection are mandatory. The LiveKit admin surface must stay on a private network.

## Caching
Redis and the provider's clustered coordination are shared infrastructure; process-local state must not decide room ownership.

## Performance / Scalability
Provisioning/reconciliation run in bounded queues. HA requires real multi-node LiveKit, routing, TURN and network-partition acceptance testing; throughput is not established by static review.

## Testing
`aos/api/live/tests/`, `aos/api/calls/tests/` and global webhook/callback tests cover token grants, room failures, state revalidation and provider event ordering. Acceptance also requires simultaneous hosts/call participants across nodes and failed-node reconnection.
