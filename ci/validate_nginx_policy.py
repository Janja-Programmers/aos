from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
	root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
	limits_path = root / "infra/nginx/snippets/rate-limits.conf"
	api_path = root / "infra/nginx/aos-api.conf.template"
	maps_path = root / "infra/nginx/maps.conf.template"
	limits = limits_path.read_text(encoding="utf-8")
	api = api_path.read_text(encoding="utf-8")
	maps = maps_path.read_text(encoding="utf-8")
	errors: list[str] = []

	if not re.search(
		r"limit_req_zone\s+\$aos_callback_rate_key\s+zone=aos_signed_callback:\d+m\s+rate=\d+r/s;", limits
	):
		errors.append("dedicated callback limit_req_zone is missing or unbounded")
	callback_rate = re.search(r"zone=aos_signed_callback:\d+m\s+rate=(\d+)r/s", limits)
	if callback_rate and not (20 <= int(callback_rate.group(1)) <= 500):
		errors.append("callback rate must be higher-capacity but bounded between 20 and 500 requests/second")
	if "$binary_remote_addr" not in limits:
		errors.append("callback rate key must use the direct network peer address")
	if re.search(r"proxy_add_x_forwarded_for|http_x_forwarded_for|http_cf_connecting_ip", limits, re.I):
		errors.append("attacker-controlled forwarded headers must not be callback rate-limit keys")
	callback_location = re.search(
		r"location\s+~\s+\^/api/method/aos\\\.api\\\.v1\\\..*?handle_callback\$\s*\{(?P<body>.*?)\n\s*\}",
		api,
		re.S,
	)
	if not callback_location:
		errors.append("dedicated signed-callback location is missing")
	else:
		body = callback_location.group("body")
		match = re.search(r"limit_req\s+zone=aos_signed_callback\s+burst=(\d+)\s+nodelay;", body)
		if not match:
			errors.append("callback location lacks bounded burst enforcement")
		elif not (20 <= int(match.group(1)) <= 2000):
			errors.append("callback burst is outside the reviewed bounded range")
		if "limit_req_status 429;" not in body:
			errors.append("callback limit must preserve HTTP 429")
		if "proxy_pass" not in body:
			errors.append("callback location does not proxy to Frappe")
	if "location ^~ /api/" in api:
		errors.append("generic ^~ API location would bypass the callback regex location")
	if "@aos_rate_limited" not in api or '"error":"RATE_LIMIT"' not in api:
		errors.append("shared sanitized rate-limit response is missing")

	# TileServer GL currently emits its own wildcard CORS header. Nginx must
	# suppress that upstream value before adding the canonical public Maps
	# header, otherwise browsers receive a combined `*, *` value and reject it.
	if maps.count("proxy_hide_header Access-Control-Allow-Origin;") < 2:
		errors.append("maps proxy locations must suppress duplicate upstream CORS headers")
	if maps.count('add_header Access-Control-Allow-Origin "*" always;') < 3:
		errors.append("maps OPTIONS and public resources must expose the reviewed wildcard CORS policy")

	if errors:
		print("\n".join(errors), file=sys.stderr)
		return 1
	print("Validated bounded signed-callback and baseline Nginx rate-limit policy.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
