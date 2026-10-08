#!/usr/bin/env bash
# Supervisor-only entry point for Frappe realtime's Unix socket.
set -Eeuo pipefail

SOCKET="/run/aos-socketio/socketio.sock"
LOCK="/run/aos-socketio/start.lock"
NODE="/home/aos/.nvm/versions/node/v24.14.1/bin/node"
ENTRY="/home/aos/frappe-bench/apps/frappe/socketio.js"

[[ -d /run/aos-socketio && -x "$NODE" && -f "$ENTRY" ]] || {
  echo "Socket.IO prerequisite missing" >&2; exit 1;
}

# Keep this exclusive lock open across exec, for the lifetime of the process.
exec 9>"$LOCK"
flock -n 9 || { echo "Socket.IO startup lock held" >&2; exit 1; }

if [[ -e "$SOCKET" || -S "$SOCKET" ]]; then
  [[ -S "$SOCKET" && ! -L "$SOCKET" ]] || {
    echo "Refusing unexpected Socket.IO socket path" >&2; exit 1;
  }
  # Never unlink a socket that ss reports as listening.
  if ss -H -x -l -n | grep -F -- "$SOCKET" >/dev/null; then
    echo "Socket.IO socket already has an active listener; refusing unlink" >&2
    exit 1
  fi
  rm -- "$SOCKET"
fi

umask 0007
exec "$NODE" "$ENTRY"
