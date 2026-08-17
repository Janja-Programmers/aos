# Verification API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `get_my_verification` | Any* | Session required | Client |
| `submit_verification` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

## Public surface

The v1 public surface remains exactly the existing two authenticated endpoints:

- `aos.api.v1.verification.submit_verification`
- `aos.api.v1.verification.get_my_verification`

Both responses keep the standard AOS response envelope. Both set private/no-store response semantics and have explicit application rate limits plus public-edge registry coverage.

### submit_verification

The authenticated Frappe session is the only source of account identity. The endpoint strips known transport fields such as `cmd`, rejects unknown business fields, validates bounded strings/enums/dates/documents, and delegates to `VerificationService`. Client-supplied User identity, request name, status, reviewer identity, approval timestamps, and profile `is_verified` are not accepted.

An optional bounded `idempotency_key` is stored only as SHA-256 metadata. A retry with the same key while the same request is Pending/Reviewing returns the existing submission; a distinct duplicate request receives a conflict.

The existing submission response remains intentionally small: request ID, verification type, status, and document type/media identifiers. Raw document numbers and private file URLs are not returned.

### get_my_verification

Returns only the authenticated account's current Verification projection and privacy-safe owner view. It exposes the request public name/ID, public `ACC-*` account ID, type/status/timestamps, masked document numbers, and rejection reason only for the owner's rejected request. It does not expose internal User IDs, `verified_by`, staff-only review identity, object-storage metadata, permanent URLs, checksums, or original private filenames.

## Verification errors

Verification domain failures use stable `VERIFICATION_*` error codes mapped through the shared AOS response layer. Raw Frappe exception text and stack traces are not returned to clients. Unexpected server failures are logged server-side and returned as `VERIFICATION_INTERNAL_ERROR`.

## Document upload path

Verification does not create a competing upload API. Clients use the existing Media init/upload/confirm flow with purpose `verification_document`, then pass confirmed Media IDs to `submit_verification`. Signed private URLs are obtained only through the existing authorized Media URL endpoint when access is permitted.
