from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TestSocketIOSupervisorContract(unittest.TestCase):
    def test_wrapper_guards_socket_and_applies_umask(self):
        script = (ROOT / "infra/nginx/socketio-start.sh").read_text()
        for fragment in ('flock -n 9', 'ss -H -x -l -n', 'rm -- "$SOCKET"', 'umask 0007', 'exec "$NODE" "$ENTRY"'):
            self.assertIn(fragment, script)
        self.assertLess(script.index('ss -H -x -l -n'), script.index('rm -- "$SOCKET"'))

    def test_installer_only_overlays_socketio_program(self):
        script = (ROOT / "infra/nginx/install-socketio-supervisor.sh").read_text()
        self.assertIn('"/etc/supervisor/conf.d/frappe-bench.conf"', script)
        self.assertIn('"/home/aos/frappe-bench/config/supervisor.conf"', script)
        self.assertIn('command=/usr/local/libexec/aos-socketio-start', script)
        self.assertIn('umask=0007', script)
        self.assertIn('before-aos-socketio', script)
        self.assertNotIn('supervisorctl restart', script)


if __name__ == "__main__":
    unittest.main()
