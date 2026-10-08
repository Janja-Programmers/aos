# Frappe Socket.IO host isolation

The staging host uses Nginx for TLS and the Frappe 17 realtime backend. TCP port 9000 MUST NOT be directly exposed. Frappe 17 supports `socketio_uds` as an alternative to `socketio_port`.

## Before cutover

- Review `/etc/nginx/conf.d` for a duplicate Bench-generated Nginx site and disable only the duplicate include; do not delete the Bench source.
- Confirm supervisor manages `frappe-bench-node-socketio`, and Nginx worker identity is `www-data`.
- Verify `setfacl`, `systemd-tmpfiles`, `curl`, `ss` and `nginx` exist.
- The AOS Nginx installer rejects an active `/etc/nginx/conf.d/frappe-bench.conf`; it supports Nginx 1.24 and 1.25.1+ HTTP/2 syntax.

## Provision persistent runtime directory

```sh
sudo install -m 0644 /home/aos/aos/infra/nginx/socketio-uds.tmpfiles.conf /etc/tmpfiles.d/aos-socketio.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/aos-socketio.conf
stat -c '%U:%G %a %n' /run/aos-socketio
getfacl /run/aos-socketio
```

The default ACL grants `www-data` access to new Unix sockets created by `aos`. **Verify actual inode permissions and a successful Nginx handshake**; a directory ACL alone does not prove connectivity.

## Coordinated staging cutover

1. Capture the existing values of `socketio_uds` and `socketio_port` in `sites/common_site_config.json`, the rendered API Nginx configuration, and supervisor status. Preserve an independent rollback copy. Do not expose credentials from common or site configuration.
2. Configure Frappe's shared config using `bench set-config -g socketio_uds /run/aos-socketio/socketio.sock`. Verify the key is a string and matches the runtime path.
3. Restart **only** `frappe-bench-node-socketio`. Confirm `test -S /run/aos-socketio/socketio.sock`, `ss -lntp` has no TCP 9000 listener, and `sudo -u www-data test -w /run/aos-socketio/socketio.sock` passes.
4. Set `FRAPPE_SOCKETIO_UDS=/run/aos-socketio/socketio.sock` in the AOS environment used by `infra/nginx/install.sh`. The installer will fail closed if the socket is missing or inaccessible. Run installer only after checking preconditions and preserving the currently working rendered site; cut over Nginx promptly and confirm `nginx -t`, `systemctl is-active nginx`.
5. Probe `https://<api-domain>/socket.io/?EIO=4&transport=polling` and test an authenticated WebSocket upgrade, notifications, chat, and incoming calls. Confirm no TCP 9000 listener and external port 9000 is inaccessible.
6. Repeat the socket test after `bench restart`, supervisor restart, and reboot; verify tmpfiles ACL and that a stale UDS does not prevent startup. On startup failure, stop the Node service, confirm no process owns the socket, then remove only that stale socket as operator action. Do not blindly delete the file before every start.

**There is a brief Socket.IO interruption during cutover.** Do not combine this with unrelated deployment actions.

## Rollback

If UDS or proxy verification fails: remove `FRAPPE_SOCKETIO_UDS` from the AOS environment; remove only the `socketio_uds` key from Frappe's common configuration (restore the previous value if it existed); restart the Socket.IO supervisor program; confirm localhost TCP 9000 works; rerun the AOS Nginx installer with its original `FRAPPE_SOCKETIO_HOST` and `FRAPPE_SOCKETIO_PORT` values; validate Nginx and all realtime paths. Avoid exposing TCP 9000 externally during rollback by restricting ingress at the provider firewall. Review full listening-port exposure before production.
