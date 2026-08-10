# Calls operations

## Scheduler

- Every minute: `aos.tasks.calls.handle_missed_calls` catches stale `initiated`/`ringing` calls if the per-call timeout job failed or was delayed.
- Every five minutes: `aos.tasks.calls.reconcile_call_rooms` retries terminal room deletion in bounded batches.
- Every five minutes: `aos.tasks.calls.reconcile_active_call_state` rechecks active call policy and conservatively reconciles missing LiveKit rooms.

All scans are bounded. Provider-wide room failures stop the current cleanup/reconciliation batch to avoid outage amplification.

## Account deletion

Account cleanup atomically ends active/ringing calls with the existing `ended` state, computes duration server-side, marks room cleanup pending, and queues a bounded post-commit cleanup set. The scheduler drains remaining rooms. No LiveKit network request is made while the account-deletion transaction is holding DB work.

## Database migration

`aos.patches.v1_0.harden_calls_subsystem` runs before `install_call_indexes`. It repairs only detectable legacy inconsistencies in 100-row batches: unknown states, self active calls, missing/duplicate room names, overlapping active calls, state/active-flag mismatch, and invalid terminal timestamps/durations. It performs no external calls, notifications, realtime, queueing, or commits.

`install_call_indexes` installs the unique room-name constraint plus participant-active, conversation-state, timeout, history, cleanup, and active-reconciliation indexes after data repair.

## Observability

Structured Calls logs record operation/outcome/reason/count/latency categories for endpoint operations, token issuance, timeout/missed handling, room cleanup, and reconciliation. They intentionally exclude passwords, sessions, JWTs, API secrets, push tokens, email/account identifiers, and request payloads.

Operational failures should be investigated using the public Call ID from application state plus provider logs; do not add token values to logs while debugging.
