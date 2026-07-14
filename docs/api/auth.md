# AOS Auth and Session API Contract

> Public auth endpoints are versioned. Use `aos.api.v1.auth.*`. Unversioned `aos.api.auth.*` methods are internal and are not supported as public HTTP endpoints.

This document describes the production auth/session/shared contract for the AOS backend auth hardening pass.

## Global response shape

Success:

```json
{
  "ok": true,
  "message": "Done.",
  "data": {}
}
```

Failure:

```json
{
  "ok": false,
  "message": "Invalid credentials.",
  "error": "INVALID_CREDENTIALS",
  "data": {}
}
```

`error` is the only public machine-readable failure key. AOS APIs must not return deprecated `code` response keys.

`data` preserves valid falsy values. `data` defaults to `{}` only when the caller passes `None`/omits data. Examples: `[]`, `false`, `0`, and `""` must remain unchanged.

## Validation contract

Auth string fields are strict. Dicts, lists, booleans, numbers, and null are rejected with `VALIDATION_ERROR` instead of being coerced to strings.

Important string fields include:

- `identifier`
- `email`
- `password`
- `current_password`
- `new_password`
- `confirm_password`
- `otp`
- `reset_token`
- `id_token`
- `confirmation`
- optional `country`, `currency`, `language`, and `reason`

Validation failure example:

```json
{
  "ok": false,
  "message": "identifier must be a string.",
  "error": "VALIDATION_ERROR",
  "data": {
    "field": "identifier"
  }
}
```

## HTTP status codes

Common mappings:

| HTTP | error |
| --- | --- |
| 401 | `INVALID_CREDENTIALS`, `SESSION_INVALID`, `TOKEN_INVALID`, `TOKEN_VERIFY_FAILED` |
| 403 | `EMAIL_NOT_VERIFIED`, `ACCOUNT_DISABLED`, `ACCOUNT_SUSPENDED`, `ACCOUNT_DELETED`, `ACCOUNT_DELETED_RESTORABLE` |
| 409 | `ALREADY_EXISTS`, `PASSWORD_MISMATCH`, `ACCOUNT_NOT_DELETED` |
| 422 | `VALIDATION_ERROR` |
| 429 | `RATE_LIMIT`, `COOLDOWN` |
| 503 | `CONFIG_ERROR`, `SERVICE_UNAVAILABLE` |

`CONFIG_ERROR` is consistently HTTP 503.

## Session model

AOS currently uses Frappe sessions.

- Clients must explicitly send `client_type` as `mobile` or `web` for login endpoints.
- Mobile clients receive `data.session.sid` intentionally for Flutter/mobile session storage.
- Web clients pass `client_type: "web"`; the response omits `sid` from JSON. The future Next.js bridge must expose the session to the browser only as a secure HttpOnly cookie.
- `/me` never returns `sid` in JSON.
- Logout is idempotent. Logging out while already logged out returns success.

## POST `aos.api.v1.auth.login`

Authentication: guest allowed.

Request:

```json
{
  "identifier": "user@example.com",
  "password": "StrongPass123!",
  "client_type": "mobile"
}
```

Accepted `client_type` values:

- `mobile` — returns `data.session.sid` intentionally for Flutter/mobile session storage.
- `web` — does not return `sid`; intended for the future Next.js HttpOnly cookie bridge.

No request field aliases are accepted. Password login requires `identifier`; `email` and `usr` are rejected.

Success response for mobile:

```json
{
  "ok": true,
  "message": "Login successful.",
  "data": {
    "session": {
      "authenticated": true,
      "expires_at": null,
      "sid": "<frappe-session-id>"
    },
    "user": {
      "id": "user@example.com",
      "email": "user@example.com",
      "full_name": "Jane Doe",
      "first_name": "Jane",
      "last_name": "Doe",
      "user_image": "/files/avatar.jpg",
      "enabled": true,
      "account_status": "Active"
    },
    "preferences": {
      "country": "Kenya",
      "language": "en",
      "currency": "USD"
    },
    "roles": ["Website User"],
    "seller": {
      "is_seller": false,
      "seller_id": null,
      "status": null
    }
  }
}
```

Success response for web is the same except `data.session.sid` is omitted.

Common failures:

| HTTP | error | Meaning |
| --- | --- | --- |
| 401 | `INVALID_CREDENTIALS` | Unknown identifier, wrong password, or deleted/restorable account before password proof. Same public message to avoid user/account-state enumeration. |
| 403 | `EMAIL_NOT_VERIFIED` | Correct password was provided and the disabled account has pending email-verification evidence. |
| 403 | `ACCOUNT_DISABLED` | Correct password was provided but the user is disabled for a reason other than pending email verification. |
| 403 | `ACCOUNT_SUSPENDED` | Correct password was provided but AOS Profile account status is suspended. |
| 403 | `ACCOUNT_DELETED` / `ACCOUNT_DELETED_RESTORABLE` | Correct password was provided and account is deleted. |
| 422 | `VALIDATION_ERROR` | Missing/invalid identifier, password, client_type, or optional bootstrap fields. |
| 429 | `RATE_LIMIT` | Per-IP or per-identifier auth throttle exceeded. |
| 503 | `CONFIG_ERROR` | Required country/language/currency defaults are invalid or no matching master data exists. |

Security notes:

- Login is rate-limited by sanitized IP and normalized identifier.
- Wrong password, unknown user, and deleted/restorable account with wrong password are not distinguishable publicly.
- Deleted/disabled/suspended/unverified account state is disclosed only after the correct password is presented.
- `EMAIL_NOT_VERIFIED` is returned only when there is actual pending email-verification evidence.
- Passwords, session IDs, reset tokens, cookies, and authorization headers must not be logged.

## GET `aos.api.v1.auth.me`

Authentication: active session required, but guest requests are allowed to reach the endpoint so the API can return a stable JSON failure.

Request body: none.

Success response:

```json
{
  "ok": true,
  "message": "Session fetched.",
  "data": {
    "session": {
      "authenticated": true,
      "expires_at": null
    },
    "user": {},
    "preferences": {},
    "roles": [],
    "seller": {}
  }
}
```

Failures:

| HTTP | error | Meaning |
| --- | --- | --- |
| 401 | `SESSION_INVALID` | Missing/expired/guest session. |
| 403 | `ACCOUNT_DISABLED` | User is disabled. |
| 403 | `ACCOUNT_SUSPENDED` | AOS Profile account status is suspended. |
| 403 | `ACCOUNT_DELETED` / `ACCOUNT_DELETED_RESTORABLE` | Account is deleted. |
| 503 | `CONFIG_ERROR` | Account is missing preference and system defaults/master-data fallback are invalid. |

`/me` repairs authenticated accounts that have a valid Frappe `User` but are missing `AOS Profile` or `AOS User Preference`, using AOS Settings defaults first, then safe existing Country/Language/Currency master-data fallback.

## POST `aos.api.v1.auth.logout`

Authentication: guest allowed.

Request body: none.

Success response:

```json
{
  "ok": true,
  "message": "Logged out successfully.",
  "data": {}
}
```

Already logged out:

```json
{
  "ok": true,
  "message": "Already logged out.",
  "data": {}
}
```

## POST `aos.api.v1.auth.register`

Authentication: guest allowed.

Request:

```json
{
  "email": "user@example.com",
  "full_name": "Jane Doe",
  "password": "StrongPass123!",
  "country": "Kenya",
  "language": "en",
  "currency": "USD"
}
```

Success:

```json
{
  "ok": true,
  "message": "OTP sent to email. Please verify to activate account.",
  "data": {}
}
```

Registration creates a disabled Frappe Website User, AOS Profile, AOS User Preference, and email-verification OTP record. The user/profile/preference/OTP state is committed before the OTP email is sent so the emailed OTP corresponds to durable database state.

## POST `aos.api.v1.auth.verify_email_otp`

Authentication: guest allowed.

Request:

```json
{
  "email": "user@example.com",
  "otp": "123456"
}
```

Success:

```json
{
  "ok": true,
  "message": "Email verified. Account activated.",
  "data": {}
}
```

Public OTP failures intentionally return the same generic failure for nonexistent account, missing OTP record, wrong OTP, used OTP, and expired OTP:

```json
{
  "ok": false,
  "message": "Invalid or expired OTP.",
  "error": "OTP_INVALID",
  "data": {}
}
```

## POST `aos.api.v1.auth.resend_email_otp`

Authentication: guest allowed.

Request:

```json
{
  "email": "user@example.com"
}
```

Success/generic response:

```json
{
  "ok": true,
  "message": "If the account is pending verification, a new OTP has been sent.",
  "data": {}
}
```

The endpoint intentionally uses a generic success message for unknown accounts, deleted accounts, or missing pending OTP records.

## Password reset endpoints

### POST `aos.api.v1.auth.forgot_password_request`

Authentication: guest allowed.

Request:

```json
{
  "email": "user@example.com"
}
```

Success/generic response:

```json
{
  "ok": true,
  "message": "If an account exists for this email, an OTP has been sent.",
  "data": {}
}
```

Deleted accounts receive the same generic response and must use the restore-account flow.

### POST `aos.api.v1.auth.forgot_password_verify_otp`

Authentication: guest allowed.

Request:

```json
{
  "email": "user@example.com",
  "otp": "123456"
}
```

Success:

```json
{
  "ok": true,
  "message": "OTP verified.",
  "data": {
    "reset_token": "<one-time-reset-token>"
  }
}
```

Public OTP failures return `OTP_INVALID` with `Invalid or expired OTP.`.

### POST `aos.api.v1.auth.forgot_password_reset`

Authentication: guest allowed.

Request:

```json
{
  "email": "user@example.com",
  "reset_token": "<one-time-reset-token>",
  "new_password": "NewStrongPass123!",
  "confirm_password": "NewStrongPass123!"
}
```

Success:

```json
{
  "ok": true,
  "message": "Password updated successfully.",
  "data": {}
}
```

## POST `aos.api.v1.auth.change_password`

Authentication: active session required.

Request:

```json
{
  "current_password": "OldStrongPass123!",
  "new_password": "NewStrongPass123!",
  "confirm_password": "NewStrongPass123!"
}
```

Success:

```json
{
  "ok": true,
  "message": "Password changed successfully.",
  "data": {}
}
```

## Account delete/restore endpoints

### POST `aos.api.v1.auth.delete_account`

Authentication: active session required.

Request:

```json
{
  "confirmation": "DELETE",
  "reason": "I no longer want to use this account"
}
```

`confirmation` must be the exact string `DELETE`.

### POST `aos.api.v1.auth.request_restore_account`

Authentication: guest allowed.

Request:

```json
{
  "email": "user@example.com"
}
```

Response is generic:

```json
{
  "ok": true,
  "message": "If a restorable account exists for this email, a restore code has been sent.",
  "data": {}
}
```

### POST `aos.api.v1.auth.restore_account`

Authentication: guest allowed.

Request:

```json
{
  "email": "user@example.com",
  "otp": "123456"
}
```

Public missing/wrong/expired restore OTP failures return `OTP_INVALID`.

## Social login endpoints

- `POST aos.api.v1.auth.google_login`
- `POST aos.api.v1.auth.apple_login`

Both require:

```json
{
  "id_token": "<provider-id-token>",
  "client_type": "mobile"
}
```

Optional `country`, `currency`, and `language` are strict strings if supplied.

Both endpoints use the same sid behavior as password login. Existing disabled users are not silently re-enabled by social login. A valid social token can create a new enabled Website User, AOS Profile, and AOS User Preference. New social-user bootstrap is atomic: if User/Profile/Preference creation fails, the request rolls back and does not leave a partial enabled account or create a session. Existing users are not deleted or rolled back if preference repair fails; the endpoint returns a safe failure response instead.

Apple token verification uses the production `AOS Settings.apple_oauth_client_ids` allowlist. Configure every valid Apple token audience here, one per line or comma-separated. For AOS this should include the mobile iOS Bundle ID and the web Service ID, for example:

```text
com.africaonlinestores.app
com.africaonlinestores.web
```

## Database contracts

Auth-related database constraints/indexes:

- `AOS User Preference.user` is unique; one preference per Frappe User.
- `AOS Profile.user` is unique by DocType field/autoname.
- `AOS Email Verification.user + purpose` has a unique index; one active row per user/purpose is reused idempotently.
- `AOS Email Verification.email + purpose + is_used` supports public OTP lookup/throttle flows.
- `AOS Email Verification.purpose + is_used + expires_at` supports expiry/status maintenance.
- `AOS Email Verification.reset_token_hash + reset_token_expires_at` supports password reset token validation/cleanup.
- `AOS Profile.account_status + is_deleted + restore_deadline` supports deleted/restorable account lookup.

Migration patch: `aos.patches.v1_0.add_auth_indexes`.

## Breaking changes

- `login` no longer returns top-level `sid`; it returns `data.session.sid` only for mobile clients.
- `login` requires `identifier`; old `email` and `usr` request aliases are rejected.
- `login` requires explicit `client_type`.
- Auth/shared failure responses use `error` only; deprecated `code` response keys are removed.
- `/me` no longer returns `sid` in JSON.
- `/me` response message is `Session fetched.`.
- Public OTP verification endpoints no longer expose `OTP_NOT_FOUND`, `OTP_EXPIRED`, or `OTP_USED`; they return `OTP_INVALID`.
- Deleted/restorable account state is no longer disclosed during login until the password is proven correct.
- `EMAIL_NOT_VERIFIED` is returned only when pending email-verification evidence exists; otherwise disabled users return `ACCOUNT_DISABLED`.
- Existing disabled users are not automatically re-enabled by Google/Apple login.
- Failed new Google/Apple social-login bootstrap does not leave a partial User, AOS Profile, or AOS User Preference.

## Frontend migration notes

- Flutter should read `sid` from `response.data.session.sid`, not from `response.sid` or `response.data.sid`.
- Flutter must call `login` with `client_type: "mobile"`.
- Next.js should call `login` with `client_type: "web"` and depend on a secure HttpOnly cookie bridge instead of reading `sid` in JavaScript.
- All clients must use `error` for branching. `code` no longer exists in failure responses.
- Bootstrap authenticated user state from `data.user`, `data.preferences`, and `data.seller` returned by both login and `/me`.
- Treat logout as always safe to call; clear local state after any successful logout response.

## Test commands

Run focused auth/shared tests:

```bash
bench --site <site-name> run-tests --app aos --module aos.api.shared.tests.test_responses
bench --site <site-name> run-tests --app aos --module aos.api.shared.tests.test_auth_helpers
bench --site <site-name> run-tests --app aos --module aos.api.auth.tests.test_session_api
bench --site <site-name> run-tests --app aos --module aos.api.auth.tests.test_register_api
bench --site <site-name> run-tests --app aos --module aos.api.auth.tests.test_otp_api
bench --site <site-name> run-tests --app aos --module aos.api.auth.tests.test_password_reset_api
bench --site <site-name> run-tests --app aos --module aos.api.auth.tests.test_delete_restore_api
bench --site <site-name> run-tests --app aos --module aos.api.auth.tests.test_social_login_api
bench --site <site-name> run-tests --app aos --module aos.tests.test_auth_database_contracts
bench --site <site-name> run-tests --app aos --doctype "AOS User Preference"
bench --site <site-name> run-tests --app aos --doctype "AOS Email Verification"
```

Run the broader core feature smoke tests affected by auth payload changes:

```bash
bench --site <site-name> run-tests --app aos --module aos.tests.test_core_feature_flows
```
