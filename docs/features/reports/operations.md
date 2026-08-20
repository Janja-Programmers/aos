# Reports operations

## Desk review

Users with effective Write permission on the relevant Report DocType use the existing Frappe Desk views. Review only from `Reviewing`; choose either `Resolved` or `Rejected`. Where the DocType has `admin_action`, an action may be selected only with `Resolved` and cannot later be changed.

Use `is_active = 0` to retire a Report Reason. Do not rename/delete reasons because report history references the canonical reason name.

## Investigation guidance

When investigating a report, use the Report record and its linked target in Desk. Do not copy report `details` into logs or public notifications. Warning actions currently have no modeled notification contract and are intentionally audit-only; operators should not assume an automated warning was sent.

## Monitoring

The Report logger emits production-safe lifecycle events such as report submission and moderation action application. Existing domain logs remain authoritative for downstream Ads/Seller/Short/Account state changes.

## Failure handling

Public creation operations use savepoints and do not own the outer transaction. Duplicate submissions return the existing domain's established duplicate/validation behavior. A failed optional Social block is rolled back to its nested savepoint while preserving the report operation.

No external provider is required by the human Report domain. Automated content-moderation workers are separate and follow their own production runbook.
