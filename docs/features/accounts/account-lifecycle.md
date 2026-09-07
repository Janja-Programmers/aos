# Account lifecycle

Supported product states are `Active`, `Deleted`, and `Suspended`.

- **Active:** normal authentication and feature access.
- **Deleted:** a reversible 30-day account tombstone. Authentication/session capability is revoked immediately while durable profile, social, marketplace, verification and personalization state is preserved but hidden. Restore returns the same account identity and durable relationships.
- **Suspended:** administrative state; authentication and self-service mutation are denied until administration changes the state.

`User.enabled` remains Frappe's authentication switch and is not a second AOS product lifecycle. Deletion/restoration locks the profile row. Repeated deletion is idempotent and reconciles access revocation.
