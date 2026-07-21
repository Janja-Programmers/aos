# Rate limiting

## Layers

1. Nginx applies a public baseline of 10 requests/second per direct network peer with `burst=40 nodelay`.
2. Signed callback endpoints use a separate higher-capacity but bounded zone of 50 requests/second with `burst=200 nodelay`.
3. Application policies retain endpoint-specific guest, authenticated, and sensitive limits and return the shared HTTP 429 error contract.
4. CI enforces the public-endpoint registry and reviewed exemptions.

Health/readiness and private metrics paths remain usable. Signed callbacks are not unlimited: edge limiting reduces invalid-HMAC floods, while application signature and timestamp validation remains mandatory.

## Trusted proxies

The default Nginx key is `$binary_remote_addr`. It does not trust attacker-controlled forwarded headers. Before placing another proxy in front, configure explicit trusted CIDRs with `set_real_ip_from` and the correct `real_ip_header`; then test that untrusted clients cannot choose the rate-limit key.

## Callback tuning

Tune callback capacity from measured peak terminal callback rate:

```text
required sustained rate >= active worker count × expected completions/second × retry margin
```

Keep a bounded burst and monitor 429s. Raising limits must not remove the zone. For private worker networks, optionally allowlist callback-producing subnets in addition to HMAC validation; do not replace HMAC with network trust.

## Validation

```bash
python ci/validate_nginx_policy.py .
python ci/render_nginx.py /tmp/aos-nginx
nginx -t -c /tmp/aos-nginx/nginx.conf -p /tmp/aos-nginx
```

Expected: the callback regex location uses `aos_signed_callback`, has a finite burst, returns HTTP 429 through the shared JSON contract, and the generic API location does not shadow it.
