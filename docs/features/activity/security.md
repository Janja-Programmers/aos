# Activity security and privacy

## Ownership and IDOR

`AOS User Activity` is private user-owned history. Public list/hide/clear operations derive the owner from the authenticated session and never accept another User as an input. Hide locks by `(activity_id, current_user)` and returns generic not-found behavior for cross-account IDs.

There is no public Activity creation endpoint. Feature producers supply server-derived targets after their own domain authorization/state checks.

## Serialization

The database row may contain legacy/internal target information needed to reconcile old history, but the public serializer exposes only semantic target kinds and navigation IDs. It allowlists metadata per canonical activity type and strips unknown values.

Identity metadata is converted to opaque public identifiers:

- account identities -> `ACC-*`
- seller identities -> `SELLER-*`

Internal User/email names, internal Seller names, Frappe DocType names, report IDs, Live analytics `session_id` / `view_id`, and arbitrary metadata are not exposed.

## Storage and Desk

Activity rows cannot be renamed, shared, exported, printed, emailed, edited, created, or deleted by the normal System Manager Desk role. System Manager retains read/report access for operational diagnosis. Website search indexing is disabled.

`active_key` is hidden/read-only and server-generated. A unique database constraint is the final integrity boundary for one active row per `(user, unique_key)`.

## Logging

Activity observability is deliberately low-cardinality and privacy-safe. It may log Activity ID, group/type, result count and outcome; it does not log User/email, search query, route ID, target title, metadata, session IDs, or arbitrary request payloads.
