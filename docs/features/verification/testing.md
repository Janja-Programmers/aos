# Verification testing

Feature-owned tests live in `aos/api/verification/tests/`.

## Pure/static hardening tests

`test_verification_source_guards.py` validates the preserved public surface, transaction ownership, strict request boundaries, exact types/states, private Media policy, serializer redaction, reviewer lifecycle enforcement, notification dedupe, account-deletion evidence cleanup, migration safety, stable error mappings, rate-limit coverage, and PII-safe observability.

Run without a Frappe site:

```bash
python -m unittest aos.api.verification.tests.test_verification_source_guards -v
```

## Frappe-backed Verification tests

`test_verification_database.py` exercises real request persistence, idempotency, private-media attachment/IDOR checks, self-approval denial, System Manager decisions, profile/Seller projection, rejection/resubmission, notification decision dedupe, evidence immutability, account-state enforcement, and account-deletion evidence cleanup. Validation, lifecycle and serializer modules are in the same feature test package. Run them on a migrated Frappe test/staging site:

```bash
bench --site <site> run-tests --app aos --module aos.api.verification
```

Follow with Accounts, Sellers, Notifications/Media regression tests because Verification projects state into those hardened domains, then run the complete AOS suite before promotion.
