# Account lifecycle

Supported states are `Active`, `Deactivated`, `Deleted`, and `Suspended`.

- **Active:** normal authentication and feature access.
- **Deactivated:** login disabled, sessions/tokens revoked, realtime activity ended, retained content not automatically removed.
- **Deleted:** recoverable soft deletion; private exposure removed, active/public feature records hidden according to policy, and restoration allowed within the configured 30-day window.
- **Suspended:** administrative state; self-service profile mutation and authentication are denied.

Transitions lock `AOS Profile`. Repeated deactivation or deletion reconciles access revocation and returns an idempotent result.
