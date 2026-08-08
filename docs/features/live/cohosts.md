# Co-host Workflows

## Product model

A Live supports one accepted or active co-host slot. The workflow record is `AOS Live CoHost`. Host ownership and candidate account are server-resolved and immutable.

Request types:

- host invitation to an active viewer;
- active viewer request to the host.

## States

```text
pending -> accepted -> active -> ended
pending -> rejected
pending -> cancelled
pending -> expired
accepted -> cancelled
accepted -> expired
```

Ending the parent Live changes active to ended and pending/accepted to cancelled.

## Authorization

- Only the host may invite.
- Preferred host invitations select an active authenticated viewer by the opaque `aos:participant:*` LiveKit identity already visible in the host room. The backend resolves the private account/session under lock.
- The legacy `target_user + session_id` invitation form remains accepted for existing clients, but new clients must not request another viewer's session ID.
- Only an active viewer may request.
- The responder is derived from request type: candidate accepts/rejects an invitation; host accepts/rejects a viewer request.
- A candidate cannot approve their own request.
- Host or initiating/target party may cancel only where the established validator allows it.
- Only host or active co-host may end an active co-host session.
- Candidate access is rechecked against canonical account and block policy on read, acceptance, activation, and token issuance.

## Concurrency and idempotency

- Live row is locked before the selected active viewer row and workflow row.
- `active_workflow_key` is unique for unresolved `(live, candidate)` state.
- Duplicate invite/request returns the existing workflow.
- Repeated response with the same terminal outcome is idempotent.
- Accept/reject runs while the Live row is locked and rejects a Live that ended concurrently.
- Acceptance revalidates the active viewer session, account state, block state, expiry, and slot.
- Cross-Live IDs and mismatched sessions fail closed.

## Expiry

Pending workflows expire after 60 seconds. Read/action paths mark stale rows expired, and the five-minute reconciliation worker performs bounded background expiry.

## LiveKit consistency

Accepted/active workflows store the server-generated participant identity. Token issuance grants publish only after accepted/active authorization. Cancellation after acceptance and active end queue participant removal after commit. A room event or token cannot recreate application role state.

## Events

Targeted user events:

- `aos_live_cohost_invited`
- `aos_live_cohost_request_received`
- `aos_live_cohost_accepted`
- `aos_live_cohost_rejected`
- `aos_live_cohost_cancelled`
- `aos_live_cohost_activated`

Public room events:

- `aos_live_cohost_started`
- `aos_live_cohost_ended`

Private payloads are sent only to the host/candidate. Public room payloads exclude session ID, LiveKit identity, and private workflow metadata. A host invitation response may echo only the opaque LiveKit identity the host already selected; it never returns the target viewer's AOS session ID.
