# Activity testing

Feature-owned tests live in `aos/api/activity/tests/`.

## Pure/source guards

Run without a Frappe site:

```bash
python -m unittest aos.api.activity.tests.test_activity_source_guards -v
```

These guards cover the exact public endpoint surface, authentication/rate limits, transaction ownership, strict validation, existing activity types only, database uniqueness/locking, privacy-safe serialization, best-effort producer hooks, Desk privacy, account deletion, migration ordering and safe observability.

## Frappe-backed behavior

On a migrated site:

```bash
bench --site <site> run-tests --app aos --module aos.api.activity
```

The DB suite covers index installation, auth, strict/bounded listing, private ownership, serializer redaction, active-row coalescing, hide idempotency/IDOR, fresh history after hide, scoped clear, and account-deletion cleanup.

Because Activity producers live in other hardened features, also run Ads, Shorts, Social, Live, Reports and Accounts/account-deletion regressions before the full AOS suite.
