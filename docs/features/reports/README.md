# Reports

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `get_report_reasons` | GET | Session required | Client |
| `report_ad` | POST | Session required | Client |
| `report_short` | POST | Session required | Client |
| `report_user` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->


## Overview

Reports is the private complaint-intake domain for three public AOS targets: **User/Account**, **Ad**, and **Short**. An authenticated account submits a canonical target identifier, a canonical classified reason identifier, and optional bounded details. Reports validates the target through the already-hardened owning feature, persists a durable target-specific report row, and exposes that row to authorized staff in Frappe Desk.

Reports does not decide generic moderation outcomes and does not mutate Account, Seller, Ad, or Short state. It records evidence for staff review and for later Moderation integration.

## Ownership

Reports owns report submission, canonical reason classification, report persistence, reporter attribution, target relationships, the shared report review lifecycle, duplicate/idempotency rules, public-safe submission projections, and Desk report records.

Reports does not own Account identity, Social blocking/privacy rules, Ad lifecycle/visibility, Short lifecycle/visibility, generic Moderation, Media, Notifications infrastructure, or Activity. Those contracts are consumed rather than recreated.

The hardened Reviews feature owns Review-report submission. `AOS Review Report` continues to consume the same reason master and shared lifecycle; `Review` is therefore an internal reason scope only. It is **not** a fourth public Reports target and `get_report_reasons` rejects `review`.

## Target Types

| Public target | Public identifier | Authoritative target boundary | Durable report DocType |
|---|---|---|---|
| `user` | `ACC-*` | Accounts public identity + account availability | `AOS User Report` |
| `ad` | Ads `public_id` (`ad_id`) | Ads `require_public_ad_for_viewer` | `AOS Ad Report` |
| `short` | `SHR-*` (`short_id`) | Shorts identity + `can_view` | `AOS Short Report` |

The three DocTypes are intentionally retained. They preserve direct Links, target-specific Desk filters, existing derived Ad report counts, and referential integrity without turning the report table into a polymorphic mega-record. Shared validation, reason classification, duplicate handling, lifecycle, errors, rate limits, and projections live in the Reports service layer.

## Reason Classification

`AOS Report Reason` uses a stable machine `reason_id` as both durable API identity and document name. Human-readable `label` is presentation data and may change without changing identity.

Allowed targets are normalized in child rows of `AOS Report Reason Target` rather than encoded into labels or duplicated reason rows. A reason can therefore apply to one or several targets. The server validates the requested reason against the target on every new submission.

The fresh-site canonical catalog reflects existing AOS product behavior and consolidates the historical `Scam or fraud` / `Suspected scam or fraud` duplication:

| Reason ID | Label | User | Ad | Short | Internal Review scope |
|---|---|:---:|:---:|:---:|:---:|
| `spam` | Spam | ✓ |  | ✓ | ✓ |
| `harassment_abuse` | Harassment or abuse | ✓ |  | ✓ | ✓ |
| `nudity_sexual_content` | Nudity or sexual content |  |  | ✓ |  |
| `violence_dangerous_content` | Violence or dangerous content |  |  | ✓ |  |
| `scam_fraud` | Scam or fraud | ✓ | ✓ | ✓ | ✓ |
| `misleading_description` | Misleading or inaccurate description |  | ✓ |  |  |
| `prohibited_restricted_item` | Prohibited or restricted item |  | ✓ |  |  |
| `inappropriate_content` | Inappropriate content |  | ✓ | ✓ | ✓ |
| `wrong_category` | Wrong category |  | ✓ |  |  |
| `misleading_pricing` | Wrong or misleading pricing |  | ✓ |  |  |
| `duplicate_ad` | Duplicate ad |  | ✓ |  |  |
| `counterfeit_product` | Counterfeit or fake product |  | ✓ |  |  |
| `other` | Other | ✓ | ✓ | ✓ | ✓ |

Disabled reasons are never returned publicly and cannot be used for new reports. Disabling a reason does not invalidate historical report rows already under review.

## Data Model

### AOS Report Reason

- `reason_id`: required, unique, immutable lowercase machine identity.
- `label`: required public display label.
- `description`: optional public-safe descriptive text.
- `icon_key`: optional presentation hint.
- `sort_order`: deterministic ordering key.
- `is_enabled`: controls use for new reports and public listing.
- `allowed_targets`: child rows containing canonical target classifications.

### User / Ad / Short report rows

Each durable row stores its target Link, authenticated `reported_by`, classified `reason`, optional `details`, `status`, staff review metadata, and a hidden `active_key` used only while the row is `Reviewing`. Ad/Short rows also retain their target-owner relationship fields used by Desk/derived projections.

Submitted evidence fields are immutable after creation. Public clients cannot set reporter identity, target-owner fields, status, review metadata, or any staff-only state.

## Naming

User, Ad, and Short reports use distributed-safe opaque UUID-backed identifiers:

- User: `URPT-<uuidhex>`
- Ad: `ARPT-<uuidhex>`
- Short: `SRPT-<uuidhex>`

No naming series or process-local counter is used. The database primary key remains the final collision boundary.

## Public API

All four endpoints require an authenticated session, set private/no-store response headers, use stable `{ok,message,error,data}` response semantics, and reject unknown request fields.

### `GET /api/method/aos.api.v1.reports.get_report_reasons`

Request:

```json
{"target_type":"user"}
```

`target_type` is exactly `user`, `ad`, or `short`. The response contains only enabled reasons classified for that target, ordered by `sort_order`, `label`, then ID. Public reason fields are `id`, `label`, `description`, and `icon_key`.

### `POST /api/method/aos.api.v1.reports.report_user`

Request fields: `account_id`, `reason_id`, optional `details`.

`account_id` must be a canonical `ACC-*` identity. Email, Frappe `User.name`, Profile aliases, client-supplied reporter fields, and report-and-block flags are not accepted.

### `POST /api/method/aos.api.v1.reports.report_ad`

Request fields: `ad_id`, `reason_id`, optional `details`.

`ad_id` is Ads `public_id`; internal `AOS Ad.name` is not a public identifier.

### `POST /api/method/aos.api.v1.reports.report_short`

Request fields: `short_id`, `reason_id`, optional `details`.

`short_id` must be the canonical `SHR-*` identity.

Successful submissions return only:

```json
{
  "report_id": "URPT-...",
  "report_type": "user",
  "target_id": "ACC-...",
  "reason_id": "harassment_abuse",
  "status": "Reviewing",
  "created_at": "...",
  "idempotent_replay": false
}
```

The projection does not expose internal reporter identifiers, internal Ad docnames, target-owner internals, staff notes, Frappe owner/modified metadata, or other users' reports.

Stable Reports errors include `REPORT_INVALID_REQUEST`, `REPORT_INVALID_TARGET`, `REPORT_INVALID_REASON`, `REPORT_REASON_NOT_ALLOWED`, `REPORT_SELF_NOT_ALLOWED`, `REPORT_ACCESS_DENIED`, and `REPORT_CONFLICT`. Guest authentication failures use the hardened shared `UNAUTHORIZED` code; rate-limit failures also continue to use the shared response contract. Clients must branch on `error`, not exception prose.

## Lifecycle

The canonical lifecycle is:

```text
Reviewing -> Resolved
          -> Rejected
```

`Reviewing` is the only initial state. `Resolved` and `Rejected` are terminal. Only an actor with effective Write permission on the concrete Report DocType may perform a transition. The server stamps `reviewed_by` and `reviewed_on`; these values cannot be supplied directly.

There is deliberately no Reports-owned `admin_action`. Generic enforcement against an Account, Seller, Ad, or Short belongs to Moderation or the owning feature, not to the complaint record.

Reporters cannot edit or delete submitted report evidence through the public API. Targets have no public report-read endpoint.

## Desk Operations

`AOS User Report`, `AOS Ad Report`, and `AOS Short Report` are staff-facing Desk records. System Manager has read/write/report/export access but cannot create, delete, share, or rename them through Desk. Creation remains an authenticated public-service responsibility.

List views expose the target, reporter, reason, and status, with standard filters on target/reporter/reason/status where appropriate. Records are sorted newest first. Staff review changes only `status`; submitted evidence and identities are read-only.

The reason master is auditable, cannot be renamed/deleted/shared, and is changed by editing the label/description/order/target scope or disabling a reason. `reason_id` is immutable.

## Privacy

Reporter identity is derived exclusively from the authenticated session and is stored for staff operations, but it is never returned to the reported target. There is no public endpoint for targets to enumerate complaints, reporter identities, details, staff status, or aggregate complaint data.

Report submission does not create Activity events. Reports does not create Social blocks. No Notifications are emitted by Reports in the current product contract.

Free-text `details` is optional, normalized, capped at 1,000 characters for User/Ad/Short reports, rejects unsafe control characters and HTML markup, and is treated as untrusted reporter input.

## Duplicate / Idempotency Rules

For **User, Ad, and Short**, exactly one `Reviewing` report may exist for a given `(reporter,target)` pair. A SHA-256 `active_key` is populated only while status is `Reviewing` and has a unique database index.

A repeated submission while that report remains `Reviewing` returns the existing report as a successful idempotent replay. Reason/details supplied on the retry do not rewrite the original evidence. After the row becomes `Resolved` or `Rejected`, its `active_key` is cleared and the same reporter may submit a new report later if the target is still reportable.

The unique `active_key` protects the invariant across workers/nodes. The normal duplicate lookup is advisory; if two workers race to insert, MariaDB uniqueness selects one winner and the loser performs a locking read of that committed winner before returning an idempotent replay. Reports deliberately does **not** lock the target row, so many different reporters do not serialize on one viral Ad/Short.

## Transactions

Each public write endpoint owns a short savepoint-scoped request transaction. The service performs authoritative target validation, reason classification, duplicate lookup, and insert without manually committing the outer request transaction. Expected failures roll back to the endpoint savepoint.

Reports does not lock target rows and performs no external network/service calls inside report creation. Activity, Notifications, media operations, and moderation enforcement are not part of the report creation transaction.

## Rate Limits

Shared Redis-backed rate limiting is used; there are no process-local counters:

- `get_report_reasons`: 60 requests/minute per authenticated user.
- each submit operation: 10 requests/minute per reporter.
- each submit operation: 3 requests/minute per reporter+target.

Rate limits are abuse controls, not duplicate-integrity controls; database uniqueness remains authoritative.

## Cross-feature Dependencies

- **Accounts**: canonical `ACC-*` target identity and active account availability.
- **Social**: existing block state is not a prerequisite for User reporting. Reporting does not create a block. Ad/Short visibility may incorporate hardened Social visibility through their owning feature.
- **Ads**: public `ad_id`, current visibility, seller ownership, and existing derived `total_reports` projection.
- **Shorts**: canonical `SHR-*`, ownership, lifecycle/moderation/audience visibility.
- **Reviews**: protected Review reporting consumes the classified reason master through an internal `Review` target scope.
- **Notifications**: no current Reports-owned notification contract.
- **Activity**: report submission is privacy-sensitive and emits no Activity record.

## Moderation Boundary

Reports owns the complaint record and staff review state only. It does not suspend accounts/sellers, hide Shorts, suspend Ads, or execute generic moderation decisions. The later Moderation hardening pass may consume Reports as evidence through a clean integration boundary.

```text
Report -> durable complaint/evidence -> later Moderation integration
```

## Account / Resource Lifecycle

A User target must resolve from canonical `ACC-*` identity to an active, enabled account at submission time. Self-reporting is rejected. Social block state does not prevent the complaint itself.

An Ad must be currently resolvable through Ads' authoritative public visibility boundary. This intentionally avoids leaking unavailable Ad state. An internal Ad docname is never accepted publicly.

A Short must be currently viewable under Shorts' authoritative `can_view` policy. Hidden/private/inaccessible content is not exposed merely because a report could reference it.

A target can become unavailable after a complaint was accepted. Historical report evidence remains reviewable in Desk; lifecycle transitions do not re-run current public visibility rules. Account-deletion cleanup removes the deleting reporter's private submitted report rows after the existing deletion lifecycle requires it, while reports *about* that account remain staff evidence.

Because AOS has no signed historical-view token, an Ad/Short that becomes unavailable immediately before submission is rejected as unavailable rather than allowing Reports to bypass the owning feature's visibility boundary.

## Testing

Reports tests cover target-scoped reason listing, disabled/mismatched reasons, canonical identities, authenticated/session-owned reporter identity, self-reporting, missing/unavailable targets, Social block behavior, strict unknown-field rejection, forged lifecycle fields, details bounds/HTML rejection, idempotent retries, database uniqueness, lifecycle authorization/terminal states, resource unavailability after submission, account-deletion cleanup, source-level privacy/moderation boundaries, migration ordering, and index presence.

Server acceptance should run:

```bash
bench --site <site> migrate
bench run-tests --app aos
```

After migration, verify the four v1 endpoints manually before moving to the separate Postman phase.
