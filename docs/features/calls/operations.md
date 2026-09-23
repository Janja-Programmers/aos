# Calls operations

## Scheduler and horizontal workers

- Every minute: `aos.tasks.calls.handle_missed_calls` performs two bounded jobs: recover old `initiated` calls whose room-provision enqueue may have been lost, then finalize dispatched unanswered calls as `missed` after the configured timeout.
- Every five minutes: `aos.tasks.calls.reconcile_call_rooms` retries durable terminal-room cleanup in bounded batches.
- Every five minutes: `aos.tasks.calls.reconcile_active_call_state` rechecks active-call account/Social policy and conservatively reconciles missing LiveKit rooms.

There is no per-call sleeping timeout worker and no process-local state required for correctness. Provision enqueue uses a stable Frappe `job_id` with native deduplication. Scans are bounded/index-driven so multiple application workers and scheduler retries remain safe.

## Database migration

The Calls migration sequence is:

1. `aos.patches.v1_0.harden_calls_subsystem`
2. `aos.patches.v1_0.install_call_indexes`
3. `aos.patches.v1_0.harden_calls_public_contract`
4. `aos.patches.v1_0.install_call_public_indexes`

The public-contract patch backfills random opaque `public_id` values and initializes `state_version`. Legacy active calls have no durable proof that the new provisioning/dispatch contract was satisfied, so they fail closed during migration rather than being treated as join-ready.

The public-index patch installs the unique public-ID index, a ring-expiry scheduler index, and a provisioning-recovery index, then removes the obsolete ringing-time timeout index. Patches perform no LiveKit/network calls, notifications, realtime publishing, queueing, or internal commits.

## Account deletion

Account cleanup atomically ends active/ringing Calls, increments `state_version`, computes duration server-side, marks room cleanup pending, and queues only bounded post-commit cleanup work. Shared reconciliation drains any remainder. No LiveKit network request is made inside the deletion transaction.

## Observability and privacy

Structured Calls logs record operation/outcome/reason/count/latency categories and intentionally exclude JWTs, secrets, sessions, push tokens, raw request payloads, and User/email identifiers. Public troubleshooting references should use the opaque `call_…` ID; internal Frappe names and provider room names remain server-side.

No new infrastructure service, Redis deployment, LiveKit endpoint, webhook, or queue type is required by this hardening.
