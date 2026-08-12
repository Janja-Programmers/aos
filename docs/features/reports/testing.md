# Reports testing

Feature-owned tests live in `aos/api/reports/tests/`.

## Pure/static hardening guards

`test_report_source_guards.py` validates the exact supported public target set, v1 transport stripping, auth/rate-limit boundaries, savepoint transaction ownership, strict request validation, target locking, database uniqueness, exact existing states/actions, reviewer lifecycle, stale-review row locks, immutable submitted evidence, moderation-service reuse, privacy-safe logging/Desk permissions, account deletion cleanup, migration safety, and rate-limit registry coverage.

Run without a Frappe site:

```bash
python -m unittest aos.api.reports.tests.test_report_source_guards -v
```

## Frappe-backed behavior

`test_report_database.py` exercises real User/Ad/Short/Review report persistence, public `ACC-*` resolution, duplicate behavior, ownership/self-report denial, report-and-block, reviewer authorization, immutable evidence, reason deactivation, terminal lifecycle, Account/Ad/Short enforcement actions, and deletion cleanup.

Run on a migrated Frappe test/staging site:

```bash
bench --site <site> run-tests --app aos --module aos.api.reports
```

Because Review reporting remains under the Reviews public API, also run:

```bash
bench --site <site> run-tests --app aos --module aos.api.reviews
```

Then run relevant Accounts, Ads, Sellers, Shorts, Social/Auth, account-deletion regressions and finally the full AOS suite before promotion.
