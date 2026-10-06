# Reports

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `get_report_reasons` | GET | Session required | Client |
| `report_ad` | POST | Session required | Client |
| `report_review` | POST | Session required | Client |
| `report_short` | POST | Session required | Client |
| `report_user` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

## Overview

Reports is the private complaint-intake and human-review domain for four public AOS targets: **User/Account**, **Ad**, **Short**, and **Review**. All four targets use the same submission, reason-classification, duplicate/idempotency, lifecycle, Desk authorization, privacy, concurrency, and audit standards.

Reports is deliberately **manual**. Submitting a report records evidence and opens a `Reviewing` case. No scheduler, moderation provider, callback, threshold, report count, classifier, or background job resolves or rejects a Report. An authorized staff member must use the explicit Desk review action to close it.

Reports does not itself suspend Accounts/Sellers, change Ad/Short/Review moderation state, remove content, or block users. Enforcement remains owned by the relevant feature or Moderation. A Report decision only closes the complaint record.

## Ownership

Reports owns:

- `AOS User Report`, `AOS Ad Report`, `AOS Short Report`, and `AOS Review Report`;
- the public `report_*` submission APIs for all four target types;
- `AOS Report Reason` and target classification;
- session-owned reporter attribution;
- target-specific visibility/reportability validation;
- active-report idempotency and database uniqueness;
- the canonical manual lifecycle;
- staff decision metadata and Desk workflow;
- public-safe report submission projections.

Reviews no longer owns a report endpoint. It only supplies the authoritative Review visibility/lifecycle boundary consumed by Reports.


## Architecture

```text
Client report action
  -> aos.api.v1.reports.report_*
  -> authentication + distributed rate limit + savepoint
  -> ReportService target adapter
  -> owning feature visibility/reportability boundary
  -> canonical reason validation
  -> active-key duplicate reconciliation
  -> target-specific AOS * Report row

Frappe Desk
  -> shared read-only Report form
  -> Resolve Report / Reject Report
  -> aos.api.internal.reports.review
  -> manual_review.review_report
  -> permission + row lock + optimistic version
  -> guarded terminal lifecycle transition
```

The public submission path and the staff decision path are deliberately separate. The public path can only create `Reviewing` evidence rows. The staff path can only close an existing `Reviewing` row and cannot mutate the reported target.

## Target Types

| Public target | Public identifier | Authoritative target boundary | Durable Report DocType | Report prefix |
|---|---|---|---|---|
| `user` | `ACC-*` | Accounts public identity + active-account policy | `AOS User Report` | `URPT-*` |
| `ad` | Ad `public_id` | Ads `require_public_ad_for_viewer` | `AOS Ad Report` | `ARPT-*` |
| `short` | `SHR-*` | Shorts identity + `can_view` | `AOS Short Report` | `SRPT-*` |
| `review` | Review `public_id` | Reviews public visibility + owning Ad visibility | `AOS Review Report` | `RREPORT-*` |

Target-specific DocTypes are retained for referential integrity and efficient Desk filtering, while all policy and lifecycle behavior is centralized in `aos.services.reports`.

## Report Reasons

`AOS Report Reason.reason_id` is the immutable API identity. `label`, `description`, `icon_key`, `sort_order`, `is_enabled`, and child `allowed_targets` are presentation/classification data.

`get_report_reasons` accepts exactly `user`, `ad`, `short`, or `review`. Only enabled reasons explicitly classified for that target are returned. Disabled reasons cannot be used for new submissions but historical reports remain reviewable.

The fresh-site reason catalog is:

| Reason ID | User | Ad | Short | Review |
|---|:---:|:---:|:---:|:---:|
| `spam` | ✓ |  | ✓ | ✓ |
| `harassment_abuse` | ✓ |  | ✓ | ✓ |
| `nudity_sexual_content` |  |  | ✓ |  |
| `violence_dangerous_content` |  |  | ✓ |  |
| `scam_fraud` | ✓ | ✓ | ✓ | ✓ |
| `misleading_description` |  | ✓ |  |  |
| `prohibited_restricted_item` |  | ✓ |  |  |
| `inappropriate_content` |  | ✓ | ✓ | ✓ |
| `wrong_category` |  | ✓ |  |  |
| `misleading_pricing` |  | ✓ |  |  |
| `duplicate_ad` |  | ✓ |  |  |
| `counterfeit_product` |  | ✓ |  |  |
| `other` | ✓ | ✓ | ✓ | ✓ |

## Public API

All endpoints require an authenticated Frappe session, set private/no-store response headers, reject unknown request fields, use shared distributed rate limits, and return the standard AOS response envelope.

### `GET /api/method/aos.api.v1.reports.get_report_reasons`

Request:

```json
{"target_type":"review"}
```

### `POST /api/method/aos.api.v1.reports.report_user`

```json
{"account_id":"ACC-...","reason_id":"harassment_abuse","details":"Optional evidence"}
```

### `POST /api/method/aos.api.v1.reports.report_ad`

```json
{"ad_id":"<ad-public-id>","reason_id":"scam_fraud","details":"Optional evidence"}
```

### `POST /api/method/aos.api.v1.reports.report_short`

```json
{"short_id":"SHR-...","reason_id":"inappropriate_content","details":"Optional evidence"}
```

### `POST /api/method/aos.api.v1.reports.report_review`

```json
{"review_id":"<review-public-id>","reason_id":"spam","details":"Optional evidence"}
```

There are no report-field aliases. In particular Review reporting uses `reason_id`, not a separate Reviews-owned `reason` contract.

Successful submissions return only:

```json
{
  "report_id":"RREPORT-...",
  "report_type":"review",
  "target_id":"<review-public-id>",
  "reason_id":"spam",
  "status":"Reviewing",
  "created_at":"...",
  "idempotent_replay":false
}
```

Internal DocType names, target-owner internals, reporter identity, staff decision fields, Frappe ownership metadata, and other users' reports are never projected to the client.

## Submission Validation

The reporter is always derived from the authenticated session. Clients cannot set `reported_by`, owner relationship fields, status, active key, reviewer identity, review timestamp, or decision note.

All report `details` fields use the same standard:

- optional;
- maximum 1,000 characters;
- Unicode-normalized;
- unsafe control characters rejected;
- HTML rejected;
- treated as untrusted evidence.

Self-reporting is rejected for every target. Target visibility is checked through the owning feature at submission time. Historical evidence remains reviewable even if the target later becomes unavailable.

## Duplicate / Idempotency Contract

Every Report type has the same invariant: exactly one active `Reviewing` report may exist for one `(reporter,target)` pair.

A hidden SHA-256 `active_key` is populated only while status is `Reviewing` and is protected by a unique database index. A retry while the active report exists returns that existing row with `idempotent_replay=true`; retry payloads do not overwrite the original reason/details. When the case becomes `Resolved` or `Rejected`, `active_key` is cleared and a later new complaint by the same reporter against the same target is allowed.

This applies equally to User, Ad, Short, and Review reports. Review reports no longer use permanent `(review,reported_by)` uniqueness.

## Manual Lifecycle

The only lifecycle is:

```text
Reviewing ──Resolve Report──> Resolved
          └─Reject Report───> Rejected
```

`Resolved` and `Rejected` are terminal.

A Report status change is accepted by the persistence layer only when it carries the internal `aos_report_manual_review` action flag set by `aos.services.reports.manual_review.review_report`. Therefore even an authorized Desk user cannot bypass the workflow by changing `status` and saving the document directly.

The manual service:

1. maps the explicit public report type to one fixed Report DocType;
2. verifies effective DocType write permission;
3. locks the Report row with `FOR UPDATE`;
4. requires the current optimistic `modified` version;
5. requires current status `Reviewing`;
6. validates `resolve` or `reject`;
7. requires a bounded rejection note for `reject`;
8. applies the terminal transition;
9. stamps `reviewed_by` and `reviewed_on` server-side;
10. clears the active duplicate key through the normal Report controller.

No automated code path carries the manual action flag.

## Desk Workflow

All four Report DocTypes have the same Desk security model:

- System Manager: read/write/report/export/print;
- no Desk create;
- no delete;
- no share;
- no rename;
- every data field is read-only, including `status`;
- submitted evidence is immutable;
- review metadata is read-only;
- the form exposes only **Resolve Report** and **Reject Report** while status is `Reviewing`.

`Reject Report` requires a `decision_note`; `Resolve Report` is confirmation-only. Both actions call the private staff endpoint `aos.api.internal.reports.review` with the report type, report ID, current version, and explicit decision. Terminal reports expose no further transition action.

`decision_note`, `reviewed_by`, and `reviewed_on` are audit metadata and cannot be edited directly.

## Manual-only / No Automatic Enforcement

Report intake may be used as evidence by staff, but Reports performs no automatic enforcement. In particular it does not:

- auto-resolve based on report count;
- auto-reject using ML/moderation results;
- suspend an Account or Seller;
- change an Ad status;
- hide/delete a Short;
- approve/reject/hide a Review;
- create a Social block;
- emit an enforcement Notification.

`AOS Ad.total_reports` remains a derived complaint count; it is not an automation trigger.

## Privacy

Reporter identity is private staff data. Reported targets have no public report-read endpoint and cannot enumerate who reported them, report details, staff notes, or case state.

Reports does not create Activity events. Report submissions and case decisions do not themselves notify the target or reporter.

## Rate Limits

- reason listing: 60/minute/authenticated user;
- report submission: 10/minute per user per report type;
- same target: 3/minute per user/report type/target.

Database uniqueness, not rate limiting, is the correctness boundary for concurrent duplicate submission.

## Database / Concurrency

All four report types use distributed-safe opaque IDs and an active-only unique key. The normal duplicate lookup is advisory; if two workers race, the database unique constraint selects the winner and the losing request locking-reads that winner and returns an idempotent replay.

Manual decisions lock the Report row and also require optimistic version equality, preventing two staff reviewers from independently closing the same case.

The report index installer migrates `AOS Review Report` away from the former permanent `(review,reported_by)` uniqueness, backfills active keys for already-Reviewing rows, removes the obsolete unique index if present, and installs `uq_aos_review_report_active`.


## Performance / Scalability

Reports is designed for horizontally scaled web workers. Submission correctness relies on database unique indexes rather than process-local locks, and duplicate races converge on the committed database winner. Staff decisions lock only the selected Report row, so unrelated reports and different reporters on a viral target do not serialize behind one target lock.

Reason listing is bounded and index-backed. Report backlog, target, reporter, and active-key indexes support Desk queues and duplicate checks. Public submission responses do not run aggregate report-list queries; the Ad `total_reports` projection remains the only target-specific derived count. Capacity still depends on production database/Redis sizing and should be verified under production-like load rather than inferred from source structure.

## Cross-feature Dependencies

- Accounts: public account identity/reportability.
- Ads: public Ad visibility and Seller relationship; derived `total_reports` projection.
- Shorts: canonical identity and visibility.
- Reviews: public Review visibility and author relationship only; Reports owns report submission/lifecycle.
- Authentication: reporter/staff session identity.
- Frappe permissions: staff review authorization.

## Testing

Reports regression coverage verifies all four public targets, canonical reason scoping, mass-assignment rejection, active-only duplicate semantics, race reconciliation, opaque IDs, immutable Desk evidence, identical detail bounds, read-only status, manual-action-only transitions, staff authorization, optimistic versioning, terminal lifecycle, migration/index contracts, privacy-safe projections, and absence of automated moderation/notification side effects.
