# Social backend

The AOS Social backend is a user-to-user **mutual-follow graph**. A one-way edge is a follow; two opposite edges are exposed as `friends`. The current data model does not contain a friend-request DocType or private-account approval state, so this hardening intentionally does not invent those workflows.

Canonical implementation: `aos/services/social/`. Stable public endpoints remain under `aos.api.v1.social` and legacy implementation imports remain compatible through thin modules in `aos/api/social/`.

Production guarantees include strict request fields and aliases, canonical `ACC-*` IDs in public output, bidirectional block enforcement, active-account filtering, bounded rate-limited discovery, signed cursors, parameterized SQL, database uniqueness, exact counter reconciliation, caller-managed transactions, atomic follow notification/outbox creation, and privacy-safe structured logs.

See the other files in this directory for contracts, lifecycle, privacy, security, migration, operations, and tests.
