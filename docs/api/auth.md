# AOS Auth and Session API Contract

This document describes the production auth/session contract for the AOS backend first hardening pass.

## Global response shape

All AOS public APIs should return the same envelope.

Success:

```json
{
  "ok": true,
  "message": "Login successful.",
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

## Session model

AOS currently uses Frappe sessions.

- Clients must explicitly send `client_type` as `mobile` or `web` for login endpoints.
- Mobile clients receive `data.session.sid` intentionally for Flutter/mobile session storage.
- Web clients pass `client_type: "web"`; the response omits `sid` from JSON. The future Next.js bridge must rely on the backend/Frappe session cookie and expose it to the browser only as a secure HttpOnly cookie.
- `/me` never returns `sid` in JSON. It only confirms and bootstraps the active session.
- Logout is idempotent. Logging out while already logged out returns success.

## POST `aos.api.auth.login`

Authentication: guest allowed.

Canonical request:

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
| 401 | `INVALID_CREDENTIALS` | Unknown identifier or wrong password. Same public message to avoid user enumeration. |
| 403 | `EMAIL_NOT_VERIFIED` | Existing account presented the correct password but has not completed email verification. |
| 403 | `ACCOUNT_DISABLED` | Existing social-login account is disabled. |
| 403 | `ACCOUNT_SUSPENDED` | AOS Profile account status is suspended. |
| 403 | `ACCOUNT_DELETED` / `ACCOUNT_DELETED_RESTORABLE` | Account is deleted and must use restore flow. |
| 422 | `VALIDATION_ERROR` | Missing/invalid identifier, password, or client_type. |
| 429 | `RATE_LIMIT` | Per-IP or per-identifier auth throttle exceeded. |
| 503 | `CONFIG_ERROR` | Required country/language/currency defaults are invalid or no matching master data exists. |

Security notes:

- Login is rate-limited by sanitized IP and normalized identifier.
- Wrong password and nonexistent users return the same public message.
- Disabled/unverified account state is only disclosed after the correct password is presented.
- Passwords, session IDs, reset tokens, cookies, and authorization headers must not be logged.

## GET `aos.api.auth.me`

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

`/me` repairs authenticated accounts that have a valid Frappe `User` but are missing `AOS Profile` or `AOS User Preference`, using AOS Settings defaults first, then safe existing Country/Language/Currency master-data fallback. It does not return internal `tabUser` fields or raw Frappe session internals.

## POST `aos.api.auth.logout`

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

If the caller is already logged out:

```json
{
  "ok": true,
  "message": "Already logged out.",
  "data": {}
}
```

## POST `aos.api.auth.register`

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

Registration creates a disabled Frappe Website User, AOS Profile, AOS User Preference, and email-verification OTP record. The account is enabled only after email OTP verification.

## POST `aos.api.auth.verify_email_otp`

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

Nonexistent users return `OTP_INVALID` rather than `NOT_FOUND` to avoid account enumeration.

## POST `aos.api.auth.resend_email_otp`

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

The endpoint intentionally uses a generic success message for unknown accounts or missing pending OTP records.

## Password reset endpoints

### POST `aos.api.auth.forgot_password_request`

Authentication: guest allowed.

Request:

```json
{"email": "user@example.com"}
```

Success/generic response:

```json
{
  "ok": true,
  "message": "If an account exists for this email, an OTP has been sent.",
  "data": {}
}
```

### POST `aos.api.auth.forgot_password_verify_otp`

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

### POST `aos.api.auth.forgot_password_reset`

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

## POST `aos.api.auth.change_password`

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

## Social login endpoints

- `POST aos.api.auth.google_login`
- `POST aos.api.auth.apple_login`

Both require `client_type` with the same sid behavior as password login. Existing disabled users are not silently re-enabled by social login. A valid social token can create a new enabled Website User, AOS Profile, and AOS User Preference.

## Breaking changes

- `login` no longer returns top-level `sid`; it returns `data.session.sid` only for mobile-style clients.
- `login` requires `identifier`; old `email` and `usr` request aliases are rejected.
- `me` no longer returns `sid` in JSON.
- `me` response message changed from `Session valid.` to `Session fetched.`.
- Auth/shared failure responses use `error` only; deprecated `code` response keys are removed.
- `verify_email_otp` and `resend_email_otp` no longer reveal `Account not found` for unknown email addresses.
- Existing disabled users are not automatically re-enabled by Google/Apple login.
- Account preferences may be auto-created during login/me for authenticated accounts missing required AOS identity rows, using system defaults.

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
bench --site <site-name> run-tests --app aos --module aos.tests.test_auth_shared_hardening
bench --site <site-name> run-tests --app aos --module aos.tests.test_response_status_mapping
bench --site <site-name> run-tests --app aos --doctype "AOS User Preference"
```

Run the broader core feature smoke tests affected by auth payload changes:

```bash
bench --site <site-name> run-tests --app aos --module aos.tests.test_core_feature_flows
```
