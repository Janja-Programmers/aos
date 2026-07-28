# Migration and deployment guide

Take and verify a database/files backup. Confirm MariaDB, Redis, workers, moderation callbacks, notification delivery and transactional-outbox readiness. Deploy the complete cumulative app, then run:

```bash
bench --site <site> migrate
bench --site <site> clear-cache
bench restart
```

## Existing-data checks

The additive Reviews patch:

- reloads Review, Review Image, Reaction and Review Report schemas;
- backfills review key, eligibility basis, moderation generation and edit count in bounded batches;
- preserves the oldest canonical legacy review per reviewer/ad;
- marks later active duplicates `Withdrawn` instead of deleting them;
- deduplicates private reaction/report action rows deterministically before adding unique constraints;
- adds public-list, author-history, moderation, reaction and report indexes;
- recalculates Ad and Seller aggregates from Approved reviews in bounded batches;
- never commits inside the patch.

Inspect migration logs for duplicate counts and confirm the unique/index definitions on staging.

## Aggregate drift dry run

Ad batches:

```bash
bench --site <site> execute aos.services.reviews.aggregates.reconcile_review_aggregates \
  --kwargs '{"dry_run": true, "batch_size": 100}'
```

Seller batches:

```bash
bench --site <site> execute aos.services.reviews.aggregates.reconcile_seller_review_aggregates \
  --kwargs '{"dry_run": true, "batch_size": 100}'
```

Continue each function with its returned `next_start_after`. Review the drift report, then repair selected batches by setting `dry_run=false`. These functions do not commit; Bench owns transaction completion.

## Post-deployment verification

- Review and report DocTypes, indexes and composite unique constraints exist.
- Legacy duplicate reviews are retained as Withdrawn, not deleted.
- Active central Report Reasons are returned by `aos.api.v1.reports.list_report_reasons`.
- Moderation service is ready; signed callbacks, replay protection and generation checks work.
- Review moderation creates canonical transactional-outbox records.
- Approval/rejection creates persistent notifications and notification-delivery outbox records without leaking review text/provider reasons.
- Pending/failed moderation, notification delivery and outbox backlogs are within operational thresholds.
- Eligibility, create, edit, withdraw, detail, public list, author list, received list, reaction and report smoke tests pass.
- Approved create/edit/withdraw/moderation transitions update Ad and Seller aggregates.
- Deleted-account reviewer identity serializes as `Deleted User`, and private reactions/reports are removed.
- Public caches/search/profile consumers no longer expose withdrawn/hidden reviews or stale rating totals.

Run the focused and full suite:

```bash
bench --site <site> run-tests --app aos --module aos.api.reviews.tests.test_eligibility
bench --site <site> run-tests --app aos
```

## Rollback

Restore the verified pre-deployment database/files backup and matching application version. Avoid schema-only rollback after clients have written new lifecycle/report data. Because the patch is additive, leaving nullable fields/indexes in place during an application rollback is safer than destructive column removal, but the rollback path must be rehearsed on staging. Review/report records created after the backup require an explicit retention/export decision before restoration.
