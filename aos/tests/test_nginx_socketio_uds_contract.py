from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
NGINX = ROOT / "infra" / "nginx"


class TestNginxSocketIODomainIsolation(unittest.TestCase):
    def test_unix_socket_upstream_is_opt_in_and_validated(self):
        installer = (NGINX / "install.sh").read_text()
        template = (NGINX / "aos-api.conf.template").read_text()
        self.assertIn('FRAPPE_SOCKETIO_UPSTREAM="http://unix:', installer)
        self.assertIn('FRAPPE_SOCKETIO_UPSTREAM="http://', installer)
        self.assertIn('sudo -u www-data test -w', installer)
        self.assertIn('[[ ! -S "${FRAPPE_SOCKETIO_UDS}" ]]', installer)
        self.assertIn('proxy_pass\n            ${FRAPPE_SOCKETIO_UPSTREAM};', template)
        self.assertNotIn('http://${FRAPPE_SOCKETIO_HOST}:${FRAPPE_SOCKETIO_PORT}', template)

    def test_nginx_http2_syntax_is_versioned_for_all_site_templates(self):
        installer = (NGINX / "install.sh").read_text()
        self.assertIn('NGINX_HTTP2_LISTEN_OPTION=" http2"', installer)
        self.assertIn('NGINX_HTTP2_DIRECTIVE="http2 on;"', installer)
        for name in ("aos-api", "maps", "minio", "livekit"):
            with self.subTest(name=name):
                template = (NGINX / f"{name}.conf.template").read_text()
                self.assertIn("ssl${NGINX_HTTP2_LISTEN_OPTION}", template)
                self.assertIn("${NGINX_HTTP2_DIRECTIVE}", template)

    def test_duplicate_bench_site_fails_closed(self):
        installer = (NGINX / "install.sh").read_text()
        self.assertIn('"${NGINX_CONF_D_DIR}/frappe-bench.conf"', installer)
        self.assertIn("Duplicate Bench Nginx include present", installer)

    def test_socket_directory_has_explicit_default_acl(self):
        config = (NGINX / "socketio-uds.tmpfiles.conf").read_text()
        self.assertIn("d /run/aos-socketio 2770 aos www-data -", config)
        self.assertIn("d:g:www-data:rwx", config)
        self.assertIn("d:o::---", config)


if __name__ == "__main__":
    unittest.main()
