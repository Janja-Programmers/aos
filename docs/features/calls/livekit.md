# Calls and shared LiveKit

Calls consumes the production-ready shared LiveKit infrastructure; it does not recreate it.

## Provisioning boundary

A Calls worker asks `aos.services.livekit.admin.ensure_room` to provision the server-generated room with `max_participants=2`. The provider network call occurs without database row locks held. After it returns, Calls re-locks/reloads the durable call, rechecks participant policy/state, and only then marks `rtc_provisioned_at` and `incoming_dispatched_at`.

If provisioning fails, the still-pending call transitions to `failed`, no incoming event is dispatched, and no join token becomes available. If a cancellation/policy change wins while provisioning is in flight, the newly created room is marked for shared cleanup instead of resurrecting the call.

## Token security

- Room name is server-generated and never accepted from the client.
- RTC identity comes from the shared Accounts public ID, never from request fields.
- Tokens are generated only by the shared `LiveKitService` and remain short-lived (existing Calls bound: 1–5 minutes).
- No room-admin grant is issued to Calls participants.
- Calls participants cannot update their own metadata.
- Audio calls may publish only `microphone` tracks.
- Video calls may publish only `microphone` and `camera` tracks.
- Participants may subscribe, but Calls tokens cannot publish LiveKit data messages; application realtime remains the signalling path.
- Receiver token issuance remains unavailable before accepted/`ongoing` state.
- After an accepted audio→video upgrade, each participant refreshes through `get_call_token`; the new video token is the only point where camera publish permission is granted.

The shared Live path continues using its existing grant semantics; the Calls source restriction is passed only by `generate_call_token`. The shared public client LiveKit endpoint/private server-admin endpoint split is unchanged.

## Cleanup and active-call reconciliation

Terminal transitions mark `room_cleanup_pending`. Deletion is queued after commit and retried by the existing bounded reconciliation scheduler. Provider cleanup failure never rewrites an already authoritative terminal transition.

For older `ongoing` calls, reconciliation observes the shared LiveKit room without DB row locks. Missing-room failure requires the existing confirmation grace period, allowing transient disconnect/reconnect. An authorized reconnect mints a new token and clears the missing-room marker.

Calls does not introduce Calls-owned LiveKit webhooks. The repository's existing signed/deduplicated Live webhook path remains owned by Live and is unchanged by this feature pass.
