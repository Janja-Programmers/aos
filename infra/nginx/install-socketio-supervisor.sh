#!/usr/bin/env bash
# Install a controlled overlay of the Bench-generated Supervisor configuration.
# Run only during a coordinated maintenance window: supervisorctl update restarts Socket.IO.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE="/home/aos/frappe-bench/config/supervisor.conf"
TARGET="/etc/supervisor/conf.d/frappe-bench.conf"
WRAPPER="/usr/local/libexec/aos-socketio-start"
SOCKET="/run/aos-socketio/socketio.sock"

[[ -f "$SOURCE" && -f "$TARGET" && -S "$SOCKET" ]] || {
  echo "Missing Supervisor config or active Socket.IO socket" >&2; exit 1;
}
sudo -u www-data test -w "$SOCKET" || {
  echo "Nginx cannot access Socket.IO socket" >&2; exit 1;
}
sudo install -d -m 0755 /usr/local/libexec
sudo install -o root -g root -m 0755 "$ROOT/infra/nginx/socketio-start.sh" "$WRAPPER"
sudo install -o root -g root -m 0644 "$ROOT/infra/nginx/socketio-uds.tmpfiles.conf" /etc/tmpfiles.d/aos-socketio.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/aos-socketio.conf

TEMP="$(mktemp)"
trap 'rm -f "$TEMP"' EXIT
python3 - "$SOURCE" "$TEMP" <<'PY'
import configparser
import sys
from pathlib import Path

src, dst = map(Path, sys.argv[1:3])
text = src.read_text()
heading = "[program:frappe-bench-node-socketio]"
assert text.count(heading) == 1, "Expected exactly one Socket.IO Supervisor program"
before, remainder = text.split(heading, 1)
body, sep, after = remainder.partition("\n[")
lines = body.splitlines()
if not any(line.startswith("user=aos") for line in lines):
    raise SystemExit("Socket.IO program user is not aos")
lines = [line for line in lines if not line.startswith(("command=", "umask="))]
lines.insert(0, "command=/usr/local/libexec/aos-socketio-start")
lines.insert(1, "umask=0007")
output = before + heading + "\n".join(lines) + ("\n[" + after if sep else "")
parser = configparser.RawConfigParser(strict=False)
parser.read_string(output)
assert parser["program:frappe-bench-node-socketio"]["umask"] == "0007"
dst.write_text(output)
PY

sudo cp -a "$TARGET" "${TARGET}.before-aos-socketio"
sudo install -o root -g root -m 0644 "$TEMP" "$TARGET"
echo "Installed Supervisor Socket.IO policy. Check diff, then run:"
echo "  sudo supervisorctl reread"
echo "  sudo supervisorctl update"
echo "Restart test requires confirming socket recreation, umask 0007, www-data HTTP 200 and public HTTP 200."
