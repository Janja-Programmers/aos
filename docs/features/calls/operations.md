# Calls operations

The minute Calls scheduler performs bounded provisioning recovery and participant ring-expiry processing. There is no sleeping worker per invited participant. Pending direct-call status reconciliation also idempotently finalizes an already-expired invite, closing the scheduler-granularity gap while keeping the database authoritative. The minute worker remains the fleet-wide safety net. Missed state is per participant; an expired invite cannot terminate a conference that already has joined participants.

The five-minute reconciliation path retries durable room cleanup and reconciles active LiveKit rooms/account/block policy. Group initiator disappearance does not make the room terminal while other joined participants remain.

## Migration

The canonical sequence is:

1. `aos.patches.v1_0.migrate_calls_to_conference_model`
2. `aos.patches.v1_0.install_call_indexes`
3. `aos.patches.v1_0.install_call_public_indexes`

The migration backfills old direct Call rows into `AOS Call Participant`, sets `initiator`, direct mode/capacity/count, then removes legacy Call-level `caller`, `receiver`, visibility, and ring-delivery columns. Current participant/public indexes are installed afterward. The migration performs no LiveKit/network calls, notifications, queueing, realtime publishing, or internal commits.

No new infrastructure is required: Calls continues using the existing database, scheduler/queues, Notifications delivery, and shared LiveKit public/private endpoint split.
