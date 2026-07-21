# Monitoring and alerting runbook

## Components

Prometheus and Alertmanager run privately by default through the repository systemd units. Prometheus loads the AOS rule file and explicitly routes alerts to Alertmanager. Companion, Frappe, backup, Redis, database, MinIO, and worker targets remain loopback/private unless an operator places them behind authenticated infrastructure.

Validate installed configuration:

```bash
promtool check config /etc/prometheus/prometheus.yml
promtool check rules /etc/prometheus/rules/aos-alerts.yml
amtool check-config /etc/alertmanager/alertmanager.yml
curl --fail http://127.0.0.1:9090/-/ready
curl --fail http://127.0.0.1:9093/-/ready
curl --silent http://127.0.0.1:9090/api/v1/alertmanagers
```

## Durable lifecycle signals

Review these low-cardinality signal families by `service_type`, `state`, or `outcome` only:

- work queued, started, completed, retryable error, retry exhaustion, terminal failure;
- durable result persisted and TTL repaired;
- callback queued, retried, completed, dead-lettered, and oldest pending age;
- callback accepted during publisher lease;
- dispatch uncertainty and reconciliation attempts/exhaustion;
- companion generation advancement;
- automatic callback-complete repair;
- manual-review transitions;
- notification uncertainty and operator resolution;
- analytics global dedupe hits;
- heartbeat staleness;
- stale callback/heartbeat index cleanup.

Never add job IDs, users, emails, URLs, callback tokens, document names, exception strings, provider IDs, or request payloads as metric labels.

## Key alerts

The rule set includes alerts for:

- metrics backend unavailable or readiness metric absent;
- required scrape target down or lifecycle metrics absent;
- API error rate/latency;
- queue backlog and stale jobs;
- callback pending too long or callback dead letter;
- callback retry scheduler stalled;
- repeated callback redispatch/reconciliation failure;
- terminal work failure/outbox mismatch;
- callback complete while Frappe remains nonterminal;
- work retry exhaustion;
- companion generation permanently ahead/manual review required;
- unresolved notification provider uncertainty;
- analytics duplicate-event attempts;
- stale worker heartbeat;
- durable lifecycle index cleanup anomalies;
- backup overdue, verification/encryption/offsite failure, and stale restore rehearsal;
- production configuration failure.

## Triage order

1. Confirm Prometheus and Alertmanager readiness and target `up` status.
2. Confirm the metrics Redis/backend is healthy.
3. Inspect Frappe outbox monitoring summary.
4. Query the signed companion status endpoint for bounded state.
5. Determine whether the problem is work execution, callback transport, generation reconciliation, durable-result loss, or provider uncertainty.
6. Repair infrastructure before replay.
7. Use callback replay for an existing terminal result; use work replay only after explicit review.

## Manual review

`Manual Review` means automatic reconciliation exhausted without enough evidence to declare external work failed. It is nonclaimable, alerts operators, and can still be resolved by an authoritative matching signed callback.

Before operator work replay, verify:

- no work/callback job is active;
- provider uncertainty is resolved;
- durable terminal result/history is understood;
- the exact outbox and stable idempotency key are supplied;
- rerunning the side effect is acceptable.

## Synthetic alert delivery

In staging:

```bash
amtool --alertmanager.url=http://127.0.0.1:9093 alert add \
  AOSSyntheticValidation severity=info environment=staging \
  summary='AOS synthetic routing validation'
```

Confirm delivery and resolution through the configured staging receiver. Never test production paging with unreviewed receiver credentials.
