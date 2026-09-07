# Privacy design

Public identity is separated from the email-backed Frappe primary key. `AOS Profile.name` is the immutable opaque `ACC-*` account ID. Deleted users serialize as tombstones without private contact details or live state; `is_deleted` in a response is derived from `account_status == "Deleted"` and is not a persisted profile field. Internal links remain intact where required for referential integrity and audit.

Private profile payloads are owner-only and sent with no-store headers. Public profile caches contain only public fields. Signed private Media URLs are not part of Accounts profile payloads. Verification documents, document numbers, reviewer notes, and rejection internals are never serialized by Accounts.

Structured account/auth correlation identifiers use a site-keyed HMAC rather than a reproducible plain hash of email or User.name.

The deletion implementation is a product retention/anonymization policy; it is not presented as proof of compliance with a particular jurisdiction.
