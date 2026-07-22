# Privacy design

Public identity is separated from the email-backed Frappe primary key. Deleted and deactivated users serialize as tombstones without private contact details or live state. Internal links remain intact for referential integrity and audit.

Private profile payloads are owner-only and sent with no-store headers. Public profile caches contain only public fields. Signed private Media URLs are not part of Accounts profile payloads. Verification documents, document numbers, reviewer notes, and rejection internals are never serialized by Accounts.

The deletion implementation is a product retention/anonymization policy; it is not presented as proof of compliance with a particular jurisdiction.
