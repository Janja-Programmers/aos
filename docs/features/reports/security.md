# Reports security and privacy

Reports can contain sensitive allegations and identify both reporter and target. They are therefore staff-only records rather than public content.

## Authorization and IDOR

Ordinary users have no DocType read/write permission for `AOS User Report`, `AOS Ad Report`, `AOS Short Report`, or `AOS Review Report`. Public APIs expose only report creation/reason listing; there is no public fetch/list endpoint that can enumerate another user's report or moderation decision.

Reporter identity is always taken from the authenticated session. Target ownership/creator/Seller relationships are derived from authoritative domain records. Target state is revalidated after row locks to prevent report creation against a concurrently deleted/suspended/hidden target.

Report DocTypes cannot be renamed, deleted, Frappe-shared, or indexed by website search after hardening, and submitted fields are immutable once review begins. `AOS Report Reason` cannot be renamed/deleted; operators use `is_active` so historical references stay stable.

## Input and abuse controls

Report creation validates a strict allowlist, strips only known Frappe transport metadata, rejects conflicting aliases, normalizes/bounds text, validates reason membership, and applies explicit per-account rate limits. The reason list is authenticated and rate limited as well.

No Report endpoint accepts arbitrary URLs, files, HTML evidence, reviewer identity, status, or moderation actions.

## Privacy and logging

Public creation responses contain the report record ID and only target identifiers already needed by the existing contract. They never return report details, reporter User IDs, reviewer identities, Desk notes, or internal moderation metadata.

Structured Report logs intentionally contain report ID, DocType, status, action and outcome only. They do not log reporter/target identity, email/phone, report details, session IDs, or arbitrary request payloads.

## Account deletion

Private report rows submitted by a recoverably deleted account are preserved during the 30-day restore window. After restore expiry, permanent cleanup removes reporter-owned User/Ad/Short/Review report rows while reports *about* the deleted account/content remain as moderation history. Ad report counters are rebuilt after reporter-owned Ad reports are removed.
