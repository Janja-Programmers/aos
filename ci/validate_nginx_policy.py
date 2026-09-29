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
	if not re.search(r"limit_req_zone\s+\$binary_remote_addr\s+zone=aos_health_probe:\d+m\s+rate=\d+r/s;", limits):
		errors.append("dedicated bounded infrastructure health rate-limit zone is missing")
	health_location = re.search(
		r"location\s+~\s+\^/api/method/aos\\\.api\\\.health\\\.\(liveness\|readiness\)\$\s*\{(?P<body>.*?)\n\s*\}",
		api,
		re.S,
	)
	if not health_location:
		errors.append("dedicated infrastructure health location is missing")
	else:
		health_body = health_location.group("body")
		if not re.search(r"limit_req\s+zone=aos_health_probe\s+burst=\d+\s+nodelay;", health_body):
			errors.append("infrastructure health location lacks its dedicated bounded limit")
		if "limit_req_status 429;" not in health_body or "proxy_pass" not in health_body:
			errors.append("infrastructure health location must preserve 429 and proxy to Frappe")
	if r'aos\.api\.v1\.diagnostics' in limits:
		errors.append("obsolete v1 diagnostics rate-limit exemption remains")
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
	basemap_start = maps.find("location ^~ /basemap/")
	basemap_end = maps.find("location / { return 404; }", basemap_start)
	basemap = maps[basemap_start:basemap_end] if basemap_start >= 0 and basemap_end > basemap_start else ""
	if "proxy_force_ranges on;" not in basemap:
		errors.append("Maps PMTiles origin must support byte ranges")
	if "proxy_http_version 1.1;" not in basemap:
		errors.append("Maps object-storage origin must use explicit HTTP/1.1 proxying")
	if "aos-proxy-common.conf" in basemap:
		errors.append("Maps object-storage origin must not inherit generic application proxy headers")
	if re.search(r"proxy_set_header\s+X-(?:Real-IP|Forwarded-[A-Za-z-]+)", basemap, re.I):
		errors.append("Maps object-storage origin must not forward application X-Forwarded/X-Real-IP headers")
	expected_host = "proxy_set_header Host 127.0.0.1:${MINIO_API_PORT};"
	if basemap.count("proxy_set_header Host ") != 1 or expected_host not in basemap:
		errors.append("Maps object-storage origin must send exactly one loopback MinIO Host header")
	if 'proxy_set_header Connection "";' not in basemap:
		errors.append("Maps object-storage origin must clear the hop-by-hop Connection header")
	if 'proxy_set_header Authorization "";' not in basemap:
		errors.append("Maps public basemap origin must strip client Authorization headers")
	if "${MAPS_OBJECT_STORAGE_BUCKET}" not in basemap or "${MINIO_API_PORT}" not in basemap:
		errors.append("Maps origin must target the private object-storage bucket")
	if basemap.count("proxy_hide_header Access-Control-Allow-Origin;") < 1:
		errors.append("Maps origin must suppress duplicate upstream CORS headers")
	if 'proxy_hide_header Accept-Ranges;' not in basemap or 'add_header Accept-Ranges "bytes" always;' not in basemap:
		errors.append("Maps origin must publish one deterministic Accept-Ranges: bytes header")
	if maps.count('add_header Access-Control-Allow-Origin "*" always;') < 2:
		errors.append("Maps OPTIONS/resource responses must expose canonical wildcard CORS")
	if "limit_except GET HEAD OPTIONS" not in basemap:
		errors.append("Maps basemap origin must reject mutating HTTP methods")

	if errors:
		print("\n".join(errors), file=sys.stderr)
		return 1
	print("Validated bounded signed-callback and baseline Nginx rate-limit policy.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
