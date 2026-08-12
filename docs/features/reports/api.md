# Reports API

## Public surface

The existing authenticated v1 surface remains:

- `aos.api.v1.reports.list_report_reasons`
- `aos.api.v1.reports.report_user`
- `aos.api.v1.reports.report_ad`
- `aos.api.v1.reports.report_short`
- `aos.api.v1.reviews.report_review`

No generic `report_target`, reviewer API, or new target endpoint was added.

All endpoints require an active authenticated AOS account, use private/no-store response semantics where applicable, have explicit application rate limits, and remain covered by the public-endpoint rate-limit registry. Known Frappe transport metadata such as `cmd` is stripped at the v1 boundary; unknown business fields are rejected.

## User reports

Accepted target aliases are the existing `target_user` / `user`; conflicting values are rejected. Public `ACC-*` account IDs and legacy references are resolved through the canonical Accounts identity service. Reporter identity always comes from the authenticated session. A user cannot report themselves or an inactive/deleted/suspended account.

The optional `block_user` / `also_block` flag preserves the existing report-and-block behavior. Blocking delegates to the Social service. It uses a nested savepoint so an optional block failure does not discard a successfully created report and no Report service commits the outer transaction.

## Ad reports

Accepted target aliases are `ad_id` / `ad`. The Ad must still be active, unexpired, and owned by an active Seller at the submission boundary. Seller ownership comes from the Ad; clients cannot choose the Seller. A Seller owner cannot report their own Ad.

## Short reports

Accepted target aliases are `short_id` / `short`. The Short must be ready, visible, and viewable by the reporter under the existing Shorts visibility policy. A creator cannot report their own Short.

## Review reports

The Reviews API preserves its existing `review_id` / `review` aliases and idempotent repeat response. The target Review row is locked and must still be Approved when the report is created. Review reporting continues to use the hardened Reviews validator/error contract and its existing uniqueness constraint.

## Report reasons

`list_report_reasons` returns only active reasons, ordered by `sort_order` then title and bounded to 200 rows. The reason master is immutable by rename/delete after hardening; operators deactivate reasons instead so historical report links remain stable.

## Responses and errors

Public endpoints preserve their existing AOS response envelopes and established error codes such as `VALIDATION_ERROR`, `NOT_FOUND`, `DUPLICATE`, Ads/Reviews domain errors, and authentication/account-state errors. Unexpected exceptions are not returned raw to clients.
