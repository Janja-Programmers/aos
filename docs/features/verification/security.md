# Verification security and privacy

Verification data is sensitive PII. The backend therefore separates public trust state, owner-visible Verification state, staff review data, and raw evidence.

## Data visibility

**Public user-visible data:** only the general verified boolean/badge already exposed by Accounts/Social/Seller serializers where applicable. Public profile APIs do not expose pending/rejected status or Verification details.

**Owner-visible private data:** `get_my_verification` returns the current account's Verification status and masked evidence identifiers. It does not return reviewer identity or raw private storage metadata.

**Staff-only data:** the full Verification Request and review fields remain protected by effective `AOS Verification Request` DocType permissions and server-side reviewer checks.

**Raw evidence:** `verification_document` Media is always Private. Verification Request and Verification Document DocTypes are not web-search indexed and cannot be renamed through normal Desk rename behavior.

## Media guarantees

The existing `verification_document` Media purpose enforces:

- private MinIO storage and private visibility;
- image/document content types and extensions only;
- 20 MiB maximum per object;
- maximum 10 media objects per Verification request;
- existing Media content/format validation during confirmation;
- owner validation before attachment;
- attachment only to `AOS Verification Request`;
- short-lived authorized signed access rather than permanent public URLs (Verification downloads are capped at 10 minutes);
- one-day orphan retention before the standard cleanup worker removes released objects.

Generic Media serialization redacts original filenames, checksums, and private storage fields for `verification_document` objects. Released/orphaned evidence is no longer readable merely because the account originally uploaded it.

Verification does not accept arbitrary document URLs and performs no server fetch of submitted business website values, so there is no document-URL SSRF path in this feature.

## IDOR and privilege boundaries

Submission and retrieval use `frappe.session.user`; no account/User ID is accepted from the client. Private Media access is re-authorized on every signed-URL request. Owners can access only their own attached Verification evidence. Reviewer access follows effective Read/Write permission on the attached `AOS Verification Request`; there is no hardcoded reviewer role exception in Media. Clients cannot approve themselves, set `is_verified`, set reviewer identity/timestamps, change another request's owner, or enumerate another account's rejection reason through Verification APIs.

## Logging and notifications

Verification structured logs use request ID/public account ID/status/outcome/count categories and never intentionally log document numbers, addresses, document contents, signed URLs, session identifiers, or full request payloads. Approval/rejection notification payloads contain only safe public identifiers and omit reviewer notes/rejection text.
