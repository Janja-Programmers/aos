from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TestNginxWebAcmeContract(unittest.TestCase):
    def test_web_host_has_http_acme_and_https_proxy(self):
        config = (ROOT / "infra/nginx/web.conf.template").read_text()
        self.assertIn("listen 80;", config)
        self.assertIn("listen [::]:80;", config)
        self.assertIn("location ^~ /.well-known/acme-challenge/", config)
        self.assertIn("root /var/www/letsencrypt;", config)
        self.assertIn("try_files $uri =404;", config)
        self.assertLess(config.index("location ^~ /.well-known/acme-challenge/"), config.index("\nserver {", config.index("location ^~ /.well-known/acme-challenge/")))
        self.assertIn("listen 443 ssl${NGINX_HTTP2_LISTEN_OPTION};", config)
        self.assertIn("listen [::]:443 ssl${NGINX_HTTP2_LISTEN_OPTION};", config)
        self.assertIn("proxy_pass http://127.0.0.1:3000;", config)
        self.assertIn("proxy_set_header Upgrade $http_upgrade;", config)
        self.assertIn("ssl_certificate /etc/letsencrypt/live/${AOS_WEB_DOMAIN}/fullchain.pem;", config)

    def test_installer_backs_up_and_restores_on_failure(self):
        script = (ROOT / "infra/nginx/install-web.sh").read_text()
        for part in (
            'sudo cp -p "$TARGET" "$BACKUP_DIR/web"',
            'sudo cp -p "$TLS_TARGET" "$BACKUP_DIR/tls"',
            'trap cleanup EXIT',
            'sudo cp -p "$BACKUP_DIR/web" "$TARGET"',
            'sudo cp -p "$BACKUP_DIR/tls" "$TLS_TARGET"',
            'sudo nginx -t',
            'sudo systemctl reload nginx',
            'RESTORE_NEEDED=true',
            'RESTORE_NEEDED=false',
        ):
            self.assertIn(part, script)
        self.assertLess(script.index('sudo nginx -t\n'), script.index('sudo systemctl reload nginx\n'))

    def test_tls_does_not_enable_obsolete_ocsp_stapling(self):
        ssl = (ROOT / "infra/nginx/snippets/ssl.conf").read_text()
        self.assertNotIn("ssl_stapling on;", ssl)
        self.assertNotIn("ssl_stapling_verify on;", ssl)
        self.assertIn("ssl_protocols TLSv1.2 TLSv1.3;", ssl)


if __name__ == "__main__":
    unittest.main()
