# Socket.IO Unix socket restart resilience

Frappe 17 listens on `/run/aos-socketio/socketio.sock` when common-site config has `socketio_uds`. The Nginx origin is the Unix socket, **not** TCP port 9000.

Supervisor loads `/etc/supervisor/conf.d/frappe-bench.conf` (regular root-owned file), not the Bench-generated `/home/aos/frappe-bench/config/supervisor.conf`. The AOS installer overlays only the Socket.IO program in a copy of the generated configuration while preserving unrelated sections. It must be rerun whenever Bench regenerates and reinstalls Supervisor config.

## Controlled install

The server must already have a working Socket.IO UDS and Nginx access (HTTP 200). Verify backups, `ss -xlpn`, and the installer source before starting. Execute:

```sh
cd /home/aos/aos
bash infra/nginx/install-socketio-supervisor.sh
sudo grep -A 14 '^\[program:frappe-bench-node-socketio\]' /etc/supervisor/conf.d/frappe-bench.conf
sudo supervisorctl reread
sudo supervisorctl update
```

The wrapper holds a startup lock across the Node process lifetime, removes a stale socket only if it is a socket inode and `ss` reports no listening owner, and refuses to remove an active socket. Other start paths that bypass the wrapper are not protected. The per-program umask is `0007`, so a newly created socket in the `aos:www-data` setgid directory is group-writable.

## Verify

After start/restart, confirm `supervisorctl status frappe-bench-web:frappe-bench-node-socketio` is RUNNING, process Umask in `/proc/<pid>/status` is 0007, socket mode is 0770, owner/group is `aos:www-data`, HTTP 200 from `sudo -u www-data curl --unix-socket /run/aos-socketio/socketio.sock 'http://localhost/socket.io/?EIO=4&transport=polling'` and from the public HTTPS origin, with no TCP 9000 listener. Verify an authenticated WebSocket, notifications, chat and calls. Repeat a controlled program restart and a bench restart; reboot test later.

## Rollback

If startup fails, examine stderr, `ss -xlpn`, and inode ownership. Do not delete active sockets. Restore `/etc/supervisor/conf.d/frappe-bench.conf.before-aos-socketio`, apply `supervisorctl reread && supervisorctl update`; for the original Node command, remove the orphaned socket only after verifying no active listener, restart the program and temporarily apply `setfacl -m u:www-data:rw` to regain Nginx connectivity. Preserve the Nginx UDS proxy throughout. Restore TCP transport only as a coordinated separate rollback with network ingress protection.
