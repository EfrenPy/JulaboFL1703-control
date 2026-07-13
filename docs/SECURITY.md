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

To authenticate the server (prevent MITM), the client must either verify the
certificate against a CA (`--tls-ca`) or pin it by SHA-256 fingerprint
(`--tls-fingerprint`). Get the fingerprint with:

```bash
openssl x509 -in server.crt -noout -fingerprint -sha256
```

`--tls` alone (no CA, no fingerprint) only encrypts — it does not authenticate
the server — and the client logs a warning to that effect.

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

## Web dashboard

`julabo-web` exposes an HTTP/WebSocket dashboard whose `/api/*` routes can
control the physical chiller (setpoint, start/stop, schedules).

- The dashboard binds to `127.0.0.1` (localhost only) by default.
- The upstream `--auth-token` authenticates the dashboard-to-TCP-server hop; it
  does **not** protect the browser-facing API. To protect the dashboard itself,
  set `--web-auth-token <secret>`.
- When `--web-auth-token` is set, every `/api/*` request and the WebSocket
  handshake must present the token via the `X-Auth-Token` header or a `?token=`
  query parameter. The static page and `/api/health` remain public.
- If the dashboard is bound to a non-loopback address without a web auth token,
  the server logs a prominent warning at startup.
- Auth tokens are compared in constant time (`hmac.compare_digest`), and the TLS
  handshake on the TCP server is bounded by a timeout to resist slow-loris DoS.

## Metrics endpoint

`--metrics-port` exposes Prometheus metrics (temperature/setpoint values,
command counters). It binds to `127.0.0.1` by default; override with
`--metrics-host 0.0.0.0` only behind a trusted network, since it is
unauthenticated.

## MQTT bridge

`julabo-mqtt` publishes status and executes any command published to
`<prefix>/command/#`.

- Use `--mqtt-tls` (optionally `--mqtt-tls-ca`) to encrypt the broker
  connection. TLS is enabled before the username/password is sent; the bridge
  warns if credentials are configured without TLS.
- The bridge performs no command-level authentication of its own — it trusts the
  broker. Restrict who may publish to the command topics with broker-side ACLs.

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
