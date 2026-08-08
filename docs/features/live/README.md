# AOS Live Backend

## Production scope

AOS Live is a Frappe-owned, LiveKit-backed public live-stream feature. The deployed product model supports:

- authenticated hosts;
- public/guest viewing subject to account availability and bidirectional blocking;
- role-scoped LiveKit tokens for host, co-host, and viewer;
- persistent viewer sessions and engagement counters;
- persistent comments and replies with committed realtime fanout;
- persistent reaction events with realtime fanout;
- one co-host slot with invite and request workflows;
- follower `live_started` notifications through bounded background fanout;
- verified LiveKit webhooks and background room reconciliation.

The implementation preserves the existing public endpoint paths and response envelope. Thin v1 wrappers validate input and delegate to the canonical Live domain services and established implementation modules.

## Existing product boundaries

The inspected repository does **not** model the following Live products, so this hardening does not invent them:

- followers-only, friends-only, private, or invite-only Live visibility;
- a public draft/scheduling API;
- persisted comments-enabled or guest/co-host-settings switches;
- recording, egress, replay, replay publication, or replay retention;
- Live reports, comment reports, moderator roles, force-end APIs, appeals, or evidence models;
- dedicated Live share/deep-link/share-count endpoints;
- comment mentions or notification preferences specific to Live;
- host transfer;
- more than one accepted/active co-host.

Camera preview, camera flipping, mute controls, and countdown state remain frontend concerns.

## Canonical modules

- `aos/api/v1/live/`: stable public API wrappers.
- `aos/api/live/`: existing implementation modules and realtime serializers.
- `aos/services/live/`: strict API boundary, policy, persistence, LiveKit, webhook, notification, participant, cursor, error, and observability helpers.
- `aos/tasks/live.py`: bounded external consistency and reconciliation work.
- `aos/aos/doctype/aos_live_*`: authoritative persistent state.
- `aos/patches/v1_0/harden_live_subsystem.py`: bounded idempotent data repair.
- `aos/patches/v1_0/install_live_indexes.py`: idempotent schema indexes after data repair.

## Key operating guarantees

- Server-generated `LIVE-*` IDs, room names, participant identities, roles, and lifecycle state.
- No service-layer `frappe.db.commit()`.
- Mutation endpoints use savepoints and restore Frappe transaction callback state on handled rollback.
- Application state is committed independently of LiveKit availability; background jobs reconcile rooms.
- Realtime events use `after_commit=True`.
- Signed opaque keyset cursors prevent tampering and cross-endpoint reuse.
- Stable `LIVE_*` errors and HTTP status semantics.
- No email, internal User name, LiveKit secret, token, raw cursor, room name, or comment text in structured Live logs.

See the companion documents in this directory for exact contracts and operations.


Live supports native Chat sharing through `aos.api.v1.live.share_live_to_chat`; Chat stores the canonical `LIVE-*` reference, not a frontend URL.
