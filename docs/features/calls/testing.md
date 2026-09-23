# Calls testing

Calls tests cover strict public inputs, opaque identities, normalized schema, server-owned 2/32 LiveKit capacity, least-privilege grants, participant-scoped lifecycle, notification suppression, cursor history, migrations/indexes, and shared Live regression safety.

Database contracts cover direct two-person behavior, 32-person conference capacity and overflow rejection, one-time provisioning fan-out, per-participant reject/missed semantics, multiple acceptors, initiator leave without conference termination, adding participants during an ongoing conference, token gating, direct end semantics, history privacy, and legacy schema removal.

After migration run the Calls unit/source/LiveKit/database modules, Notifications source guards, shared Live tests, then the complete AOS backend suite. Provider calls should remain mocked in DB contract tests; ordinary fixtures must stay transaction-local.
