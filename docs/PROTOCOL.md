# TCP JSON Protocol

The Julabo Control TCP server (`julabo-server` / `julabo-server-async`)
communicates using newline-delimited JSON messages over a persistent TCP
connection. This document describes protocol version **2**.

## Message Format

**Request** — one JSON object per line (terminated by `\n`):

```json
{"command": "temperature", "token": "secret"}
```

**Response** — one JSON object per line:

```json
{"status": "ok", "result": 21.35, "protocol_version": 2}
```

| Field              | Type   | Description                                 |
|--------------------|--------|---------------------------------------------|
| `command`          | string | Required. The command name.                 |
| `token`            | string | Required when server auth is enabled.       |
| `value`            | any    | Parameter for commands that accept a value. |
| `chiller_id`       | string | Optional. Routes to a named chiller (default: `"default"`). |
| `csv`              | string | CSV payload for `load_schedule`.            |

## Response Envelope

| Field              | Type   | Description                                |
|--------------------|--------|--------------------------------------------|
| `status`           | string | `"ok"` or `"error"`.                       |
| `result`           | any    | Command-specific return value (on success). |
| `error`            | string | Human-readable error message (on error).   |
| `protocol_version` | int    | Server protocol version (currently `2`).   |

## Commands

### Read Commands

#### `identify`

Returns the chiller identification string.

```json
// Request
{"command": "identify", "token": "secret"}
// Response
{"status": "ok", "result": "JULABO FL1703", "protocol_version": 2}
```

#### `status`

Returns the chiller status code (e.g. `"01 OK"`).

```json
{"command": "status", "token": "secret"}
{"status": "ok", "result": "01 OK", "protocol_version": 2}
```

#### `get_setpoint`

Returns the current temperature setpoint in degrees Celsius.

```json
{"command": "get_setpoint", "token": "secret"}
{"status": "ok", "result": 25.0, "protocol_version": 2}
```

#### `temperature`

Returns the current bath temperature in degrees Celsius.

```json
{"command": "temperature", "token": "secret"}
{"status": "ok", "result": 24.85, "protocol_version": 2}
```

#### `is_running`

Returns `true` if the circulation pump is running, `false` otherwise.

```json
{"command": "is_running", "token": "secret"}
{"status": "ok", "result": false, "protocol_version": 2}
```

#### `status_all`

Returns a composite object with status, temperature, setpoint, and running
state in a single round trip.

```json
{"command": "status_all", "token": "secret"}
{"status": "ok", "result": {"status": "01 OK", "temperature": 24.85, "setpoint": 25.0, "is_running": true}, "protocol_version": 2}
```

#### `ping`

Health check. Returns `"pong"` without touching the serial bus.

```json
{"command": "ping", "token": "secret"}
{"status": "ok", "result": "pong", "protocol_version": 2}
```

### Write Commands

Write commands are blocked when the server is in read-only mode.

#### `set_setpoint`

Sets the temperature setpoint. Requires a numeric `value`.

```json
{"command": "set_setpoint", "value": 30.0, "token": "secret"}
{"status": "ok", "result": 30.0, "protocol_version": 2}
```

#### `start`

Starts the circulation pump.

```json
{"command": "start", "token": "secret"}
{"status": "ok", "result": true, "protocol_version": 2}
```

#### `stop`

Stops the circulation pump.

```json
{"command": "stop", "token": "secret"}
{"status": "ok", "result": false, "protocol_version": 2}
```

#### `set_running`

Sets the pump state to the given boolean value.

```json
{"command": "set_running", "value": true, "token": "secret"}
{"status": "ok", "result": true, "protocol_version": 2}
```

The `value` field accepts booleans, numbers (`0`/`1`), and strings
(`"true"`, `"false"`, `"start"`, `"stop"`, `"on"`, `"off"`, etc.).

### Schedule Commands

#### `load_schedule`

Upload a CSV schedule. The `csv` field contains the CSV text with columns
`elapsed_minutes` and `temperature_c`.

```json
{"command": "load_schedule", "csv": "elapsed_minutes,temperature_c\n0,20\n30,40", "token": "secret"}
{"status": "ok", "result": {"steps": 2, "duration_minutes": 30.0}, "protocol_version": 2}
```

#### `stop_schedule`

Stop a running schedule.

```json
{"command": "stop_schedule", "token": "secret"}
{"status": "ok", "result": "stopped", "protocol_version": 2}
```

#### `schedule_status`

Query the current schedule status.

```json
{"command": "schedule_status", "token": "secret"}
{"status": "ok", "result": {"running": true, "elapsed_minutes": 5.2, "total_minutes": 30.0, "current_target": 23.47, "progress_pct": 17.3}, "protocol_version": 2}
```

## Error Codes

Errors are returned with `"status": "error"`:

```json
{"status": "error", "error": "Authentication failed"}
```

| Error message                       | Cause                          |
|-------------------------------------|--------------------------------|
| `Authentication failed`             | Invalid or missing auth token  |
| `Invalid request: ...`              | Malformed command or parameter |
| `Invalid argument type`             | Wrong parameter type           |
| `Device timeout`                    | Serial communication timeout   |
| `Device error: ...`                 | Chiller reported an error      |
| `Server is in read-only mode`       | Write command on read-only     |
| `Rate limit exceeded`               | Too many requests from this IP |
| `Message too large`                 | Request exceeded 1 MB limit    |
| `Serial connection lost, reconnecting...` | Serial port disconnected |
| `Internal server error`             | Unexpected server error        |

## Authentication

When the server is started with `--auth-token`, every request must include a
`"token"` field matching the configured token. Requests with a missing or
incorrect token receive an `"Authentication failed"` error.

## Multi-Chiller Routing

When multiple chillers are registered via `JulaboTCPServer.add_chiller()`,
include `"chiller_id"` in the request to route to a specific chiller. Omitting
it routes to `"default"`.

```json
{"command": "temperature", "chiller_id": "chiller-2", "token": "secret"}
```

## Protocol Versioning

The `protocol_version` field in responses indicates the server's protocol
version. Clients should log a warning if the server version is higher than
what they support. The current version is **2** (added in v0.4.0 with
schedule commands).

## Connection Limits

- Maximum message size: **1 MB** (1,048,576 bytes)
- Per-IP rate limiting: configurable via `--rate-limit` (requests per minute)
- Idle timeout: configurable via `--idle-timeout` (seconds)
