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

	# Maps origin serves only immutable/read-only PMTiles artifacts from private
	# object storage. Preserve byte-range requests and keep CORS canonical.
	if "TILESERVER" in maps or "tileserver" in maps.lower():
		errors.append("legacy TileServer dependency remains in Maps Nginx origin")
	if "location ^~ /basemap/" not in maps:
		errors.append("Maps origin must expose only the /basemap/ object prefix")
	if "proxy_force_ranges on;" not in maps:
		errors.append("Maps PMTiles origin must support byte ranges")
	if "${MAPS_OBJECT_STORAGE_BUCKET}" not in maps or "${MINIO_API_PORT}" not in maps:
		errors.append("Maps origin must target the private object-storage bucket")
	if maps.count("proxy_hide_header Access-Control-Allow-Origin;") < 1:
		errors.append("Maps origin must suppress duplicate upstream CORS headers")
	if maps.count('add_header Access-Control-Allow-Origin "*" always;') < 2:
		errors.append("Maps OPTIONS/resource responses must expose canonical wildcard CORS")
	if "limit_except GET HEAD OPTIONS" not in maps:
		errors.append("Maps basemap origin must reject mutating HTTP methods")

	if errors:
		print("\n".join(errors), file=sys.stderr)
		return 1
	print("Validated bounded signed-callback and baseline Nginx rate-limit policy.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
