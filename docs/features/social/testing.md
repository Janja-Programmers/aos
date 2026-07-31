# Testing

Automated coverage is under `aos/api/social/tests/` and existing core feature, uniqueness, migration, SQL-safety, account-deletion, seller, profile, live, shorts, chat, and notification suites.

Required checks include strict fields/aliases, self-actions, idempotent set semantics, mutual-follow friends, public serializer safety, block side effects, blocked discovery, cursor tamper rejection, search bounds, suspended targets, operation-savepoint isolation, preservation of prior caller writes after handled Social errors, transaction rollback when notification/outbox creation fails, callback restoration, wrapper stability, rate-limit coverage, parameterized SQL, patch registration, and no service/migration commits.

## Curl/Postman guide

Authenticate first and retain the Frappe session cookie.

```bash
curl -b cookies.txt -X POST \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"ACC-XXXXXXXXXXXXXXXXXXXX","action":"follow"}' \
  'https://<host>/api/method/aos.api.v1.social.toggle_follow'

curl -b cookies.txt \
  'https://<host>/api/method/aos.api.v1.social.get_following?limit=20'

curl -b cookies.txt \
  --get --data-urlencode 'query=Jane Doe' --data 'limit=20' \
  'https://<host>/api/method/aos.api.v1.social.search_users'

curl -b cookies.txt -X POST \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"ACC-XXXXXXXXXXXXXXXXXXXX","reason":"spam"}' \
  'https://<host>/api/method/aos.api.v1.social.block_user'
```

Copy `next_cursor` exactly and URL-encode it. Confirm a modified character returns HTTP 422 and `SOCIAL_INVALID_CURSOR`.
