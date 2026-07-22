# Accounts security

- Session identity is authoritative for every “my account” operation.
- No endpoint accepts a client-supplied owner/user to choose the row being mutated.
- Public account references are opaque and non-enumerating.
- Public serializers exclude email, phone, date of birth, roles, raw Media IDs, lifecycle metadata, verification documents, reviewer details, and internal flags.
- Block policy is evaluated before public profile serialization.
- Avatar Media must belong to the authenticated user, have purpose `profile_image`, be attachable, and target the same `AOS Profile`.
- Lifecycle transitions fail closed if session/token revocation fails.
- Logs hash internal user references and metrics use fixed bounded labels.
