# Security Guide

Best practices for securing Julabo Control Suite in production.

## TLS encryption

### Generate a self-signed certificate

```bash
openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout server.key -out server.crt \
  -days 365 -subj "/CN=julabo-server"
```

### Start the server with TLS

```bash
julabo-server --tls-cert server.crt --tls-key server.key
```

Clients must connect using SSL. The remote GUI (`julabo-remote`) supports a
`--tls` flag.

For production use, obtain a certificate from a trusted CA or your
organisation's internal CA.

## Authentication

The server supports token-based authentication. Clients must include the token
in every JSON request (`"token": "..."` field).

### Token priority

The server resolves the auth token in this order (first match wins):

1. `--auth-token SECRET` (CLI argument)
2. `--auth-token-file /path/to/file` (reads first line from file)
3. `JULABO_AUTH_TOKEN` environment variable
4. `auth_token` key in the config file (`~/.julabo_control.ini`)

### Recommendations

- Use `--auth-token-file` to avoid exposing the token in process listings.
- Set restrictive file permissions: `chmod 600 /etc/julabo/token`.
- Rotate tokens periodically.

## Read-only mode

Start the server with `--read-only` to reject all write commands
(`set_setpoint`, `start`, `stop`, `set_running`). This is useful for
monitoring dashboards that should not control the chiller.

```bash
julabo-server --read-only --host 0.0.0.0
```

## Network recommendations

- Bind to `127.0.0.1` (the default) when only local access is needed.
- Use `--host 0.0.0.0` only when remote clients need access, and combine
  with `--auth-token` and TLS.
- Place the server behind a firewall or VPN for lab network deployments.
- Use `--rate-limit` to protect against runaway clients (e.g. `--rate-limit 60`
  for 60 requests per IP per minute).
- Use `--idle-timeout` to automatically disconnect stale connections.

## Audit logging

Enable `--audit-log /var/log/julabo-audit.log` to record all write commands
with timestamps and client IPs.
