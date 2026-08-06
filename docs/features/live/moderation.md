# Live Moderation and Safety

## Implemented safety boundaries

- Only active accounts can host or interact.
- Bidirectional blocks are enforced across discovery, access, tokens, interactions, co-host workflows, and notification fanout.
- Host can remove an active co-host and remove comments under the established ownership rule.
- Co-host participant removal is performed after commit through LiveKit admin API.
- Disabling, suspending, deleting, or restricting a host makes the Live inaccessible and causes bounded reconciliation to end it.
- Public errors are sanitized and do not expose stack traces or upstream exception text.
- Structured logs contain only bounded operation/result categories.

## Not modeled

The repository contains no Live-specific:

- report Live/host/comment endpoint or report DocType;
- moderator role or auditable force-end endpoint;
- participant mute/kick/ban workflow beyond co-host removal;
- restricted-word/moderation-service integration for comment text;
- evidence, appeal, restoration, or enforcement-detail model.

These behaviors are intentionally deferred. They should be implemented only after canonical Moderation contracts, public error semantics, notification behavior, and evidence retention are defined.

## Emergency operations

An operator may disable/suspend the host through the canonical account/moderation path. The next five-minute reconciliation pass ends the Live and queues room deletion. For immediate incident response, run the reconciliation job manually after the account action:

```bash
bench --site <site> execute aos.tasks.live.reconcile_live_state
```

Do not edit Live status/room directly in SQL outside a documented recovery procedure.
