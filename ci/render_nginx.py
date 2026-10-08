from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

VALUES = {
	"AOS_API_DOMAIN": "api.invalid",
	"AOS_LIVEKIT_DOMAIN": "live.invalid",
	"AOS_MAPS_DOMAIN": "maps.invalid",
	"AOS_MINIO_DOMAIN": "files.invalid",
	"FRAPPE_SITE_NAME": "ci.invalid",
	"FRAPPE_SOCKETIO_HOST": "127.0.0.1",
	"FRAPPE_SOCKETIO_PORT": "9000",
	"FRAPPE_SOCKETIO_UPSTREAM": "http://127.0.0.1:9000",
	"NGINX_HTTP2_LISTEN_OPTION": " http2",
	"NGINX_HTTP2_DIRECTIVE": "",
	"FRAPPE_WEB_HOST": "127.0.0.1",
	"FRAPPE_WEB_PORT": "8000",
	"LIVEKIT_PORT": "7880",
	"MINIO_API_PORT": "9000",
	"NGINX_CLIENT_MAX_BODY_SIZE": "20m",
	"NGINX_PROXY_CONNECT_TIMEOUT": "5s",
	"NGINX_PROXY_READ_TIMEOUT": "60s",
	"NGINX_PROXY_SEND_TIMEOUT": "60s",
	"MAPS_OBJECT_STORAGE_BUCKET": "aos-maps",
}
UNRESOLVED = re.compile(r"\$\{[A-Z0-9_]+\}")


def render(text: str) -> str:
	for name, value in VALUES.items():
		text = text.replace(f"${{{name}}}", value)
	remaining = sorted(set(UNRESOLVED.findall(text)))
	if remaining:
		raise ValueError(f"unresolved variables: {', '.join(remaining)}")
	return text


def main() -> int:
	if len(sys.argv) != 2:
		raise SystemExit("usage: render_nginx.py OUTPUT_DIRECTORY")
	root = Path(__file__).resolve().parents[1]
	source = root / "infra" / "nginx"
	output = Path(sys.argv[1]).resolve()
	(output / "conf.d").mkdir(parents=True, exist_ok=True)
	(output / "snippets").mkdir(parents=True, exist_ok=True)

	for template in sorted(source.glob("*.conf.template")):
		if template.name == "acme-bootstrap.conf.template":
			continue
		content = render(template.read_text(encoding="utf-8"))
		(output / "conf.d" / template.name.removesuffix(".template")).write_text(content, encoding="utf-8")
	for template in sorted((source / "snippets").glob("*.conf.template")):
		content = render(template.read_text(encoding="utf-8"))
		(output / "snippets" / f"aos-{template.name.removesuffix('.template')}").write_text(
			content, encoding="utf-8"
		)
	for static in sorted((source / "snippets").glob("*.conf")):
		if static.name == "rate-limits.conf":
			shutil.copyfile(static, output / "conf.d" / "aos-rate-limits.conf")
		else:
			shutil.copyfile(static, output / "snippets" / f"aos-{static.name}")
	print(output)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
