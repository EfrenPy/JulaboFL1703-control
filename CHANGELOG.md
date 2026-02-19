# Changelog

## v0.8.0

### Architecture

- **Shared command dispatcher:** New `julabo_control.dispatch` module with
  `dispatch_command()` used by both `remote_server` and `async_server`,
  eliminating duplicated command handling logic.
- **Deduplicated auth token resolver:** `remote_client` now imports
  `resolve_auth_token` from `remote_server` instead of defining its own copy.
- **Lazy GUI imports:** `__init__.py` defers `run_gui` and `BaseChillerApp`
  imports via `__getattr__`, avoiding tkinter/matplotlib on headless systems.
- **Refactored `main()`:** Extracted `_setup_logging()`, `_connect_serial()`,
  and `_create_server()` helpers from the 178-line server entry point.

### Bug Fixes

- **Metrics wired in `main()`:** The Prometheus metrics HTTP server is now
  properly initialized and started when `--metrics-port` is supplied.
- **OpenAPI version dynamic:** `web.py` uses `__version__` instead of a
  hardcoded string in the OpenAPI spec.

### Testing

- Added `websockets` to the `dev` extras for WebSocket test support.
- 11 new async server dispatch tests covering all core command branches.
- 5 new WebSocket handler tests for start, stop, unknown command, missing
  value, and status push exceptions.

### Docs & Packaging

- `CHANGELOG.md` entries for v0.6.0, v0.7.0, and v0.8.0.
- `CONTRIBUTING.md` updated with development workflow and pre-PR checklist.
- New `docs/PROTOCOL.md` documenting the full TCP JSON protocol.
- `.dockerignore` updated to exclude `docs/` and `.hypothesis/`.
- CI workflows use pip caching for faster runs.

## v0.7.0

### Features

- **Async TCP server:** New `julabo_control.async_server` module with
  `AsyncJulaboServer` — an asyncio-based alternative that offloads blocking
  serial calls to a thread-pool executor. Entry point: `julabo-server-async`.
- **WebSocket support:** `JulaboWebServer` accepts a `ws_port` option that
  starts a WebSocket server pushing live status updates and accepting
  `set_setpoint`, `start`, and `stop` commands.
- **SQLite temperature history:** New `julabo_control.db` module with
  `TemperatureDB` for persisting readings. Web dashboard exposes
  `GET /api/history?minutes=N` for querying stored data.
- **Health endpoint:** `GET /api/health` returns server version without
  touching the chiller.
- **OpenAPI spec:** `GET /api/openapi.json` serves a machine-readable API
  description.
- **Versioned API paths:** All `/api/` endpoints are mirrored under `/api/v1/`
  for forward-compatible clients.

### Testing

- 571 tests at 95.6% overall coverage.
- Async server test suite with rate limiting, concurrent clients, oversized
  messages, and connection reset tests.
- WebSocket round-trip tests using `websockets.sync.client`.
- Web dashboard tests for history endpoint and health check.
- E2E simulator tests expanded.

## v0.6.0

### Features

- **MQTT bridge:** `julabo-mqtt` entry point publishes chiller telemetry to an
  MQTT broker and subscribes to command topics for remote control.
- **Config hot-reload:** `JulaboTCPServer.reload_config()` reloads mutable
  settings at runtime; SIGHUP triggers automatic reload on Unix.
- **Multi-chiller support:** A single server can proxy multiple chillers via
  `add_chiller(id, chiller)` with client-side `chiller_id` routing.
- **Server-Sent Events:** `GET /api/events` streams live status from the web
  dashboard with automatic polling fallback.
- **Schedule web UI:** Upload, stop, and monitor temperature schedules from
  the browser dashboard.

### Quality

- Dead code removal and narrower type ignores.
- CI coverage gate enforced at 90%.

### Testing

- 491 tests, >93% coverage.
- Integration tests with `FakeChillerBackend`, real `JulaboTCPServer`, and
  `RemoteChillerClient`.

## v0.5.0

### Features

- **Server-Sent Events:** `GET /api/events` SSE endpoint on the web dashboard
  streams live status updates, with automatic fallback to polling on disconnect.
- **MQTT bridge:** New `julabo-mqtt` entry point publishes chiller status to an
  MQTT broker and dispatches `command/setpoint`, `command/start`, `command/stop`
  messages. Install with `pip install julabo-control[mqtt]`.
- **Config hot-reload:** `JulaboTCPServer.reload_config()` reloads mutable
  settings (`rate_limit`, `read_only`, `idle_timeout`) at runtime. SIGHUP
  handler on Unix triggers automatic reload.
- **Multi-chiller support:** `JulaboTCPServer.add_chiller(id, chiller)` lets a
  single server proxy multiple chillers. Clients include `"chiller_id"` in
  requests; omitting it routes to the default chiller (backward compatible).
- **Schedule builder web UI:** `POST /api/schedule` (upload CSV),
  `DELETE /api/schedule` (stop), and `GET /api/schedule/status` endpoints.
  The HTML dashboard includes a textarea and Upload/Stop buttons.
- **Public API exports:** `RemoteChillerClient`, `FakeChillerBackend`, and
  `BaseChillerApp` are now importable from `julabo_control`.

### Quality

- **Dead code removal:** Removed unused `_MetricsHandler` class,
  `_MetricsState.record_error()` method, and `errors_total` field.
- **Type safety:** Removed 7 of 14 `type: ignore` comments in
  `remote_client.py` and `gui.py` by adding runtime assertions.
- **Python 3.10 in CI:** Added to the test matrix alongside 3.9, 3.11, 3.12.
- **CI coverage gate:** `--cov-fail-under=90` enforced in both CI and publish
  workflows.
- **Config docstring:** Updated `load_config()` docstring to list all supported
  sections including `[web]` and `[mqtt]`.

### Testing

- **Coverage push:** `remote_server.py` coverage improved from ~78% to ~95%.
  New tests for audit old-value exceptions, metrics recording, schedule ticker
  paths, schedule stop/status with active runner, serial watchdog threads,
  `parse_arguments`, and `configure_logging`.
- **Web coverage gaps filled:** 6 new tests for missing value, exception paths,
  unknown POST path, and empty content-length body.
- **Integration tests with simulator:** 8 new end-to-end tests using
  `FakeChillerBackend` + real `JulaboTCPServer` + `RemoteChillerClient`.
  Also covers multi-chiller routing, auth rejection, and read-only mode.
- **Total: 491 tests, projected >93% coverage.**

## v0.4.0

### Features

- **FakeChiller simulator:** New `julabo_control.simulator` module with
  `FakeChillerBackend` (duck-typed `JulaboChiller` replacement) and
  `SerialSimulator` (PTY-based serial emulator for Unix). Entry point:
  `julabo-simulator`.
- **Web UI:** Browser-based dashboard (`julabo-web`) with Chart.js temperature
  history, setpoint/start/stop controls, and 5-second auto-refresh. Proxies to
  the TCP server via `RemoteChillerClient`.
- **CLI monitor:** `julabo monitor` subcommand for live terminal temperature
  display with optional CSV logging, overwrite mode, and finite count.
- **Remote schedule support:** Protocol v2 adds `load_schedule`,
  `stop_schedule`, and `schedule_status` commands. Remote clients can now run
  temperature ramps.
- **Alarm logging:** `--alarm-log` flag persists alarm/clear events to a CSV
  file with UTC timestamps, temperature, setpoint, and deviation.
- **Prometheus metrics:** `--metrics-port` on the server exposes
  `/metrics` in Prometheus text exposition format with temperature gauges,
  command counters, and latency histograms (stdlib only, no external deps).
- **Structured JSON logging:** `--log-format json` on the server outputs
  JSON-lines log entries compatible with Loki, ELK, and `jq`.

### Server Reliability

- **Read-only mode:** `--read-only` rejects write commands (set_setpoint,
  start, stop) while allowing monitoring.
- **Idle connection timeout:** `--idle-timeout` disconnects stale TCP clients
  after the specified number of seconds.
- **Command audit log:** `--audit-log` records all write commands with
  timestamps, client IP, old/new values.
- **Serial watchdog:** Background thread monitors serial health and
  auto-reconnects with exponential backoff when the USB cable is disconnected.

### Deployment

- **PyPI CI:** GitHub Actions workflow (`publish.yml`) runs tests on tag push,
  builds the wheel, and publishes to PyPI via trusted publishing.
- **Docker Compose:** `docker-compose.yml` bundles the server with Prometheus
  and Grafana, including a pre-built dashboard.
- **Systemd unit:** `examples/julabo-server.service` with security hardening
  (NoNewPrivileges, ProtectSystem, PrivateTmp).

### Testing

- **Coverage push:** 78% to 93% overall coverage. 75 new tests across gui.py
  (63% to 100%), cli.py (58% to 99%), core.py (74% to 100%), and
  remote_client.py (68% to 99%). Total: 428 tests.

### Cleanup

- Removed legacy `requirements.txt` (everything is in `pyproject.toml`).

## v0.3.0

### Security

- **Narrowed exception handlers:** 16 broad `except Exception` blocks replaced
  with specific exception types across `gui.py`, `remote_client.py`, and `ui.py`.
  `KeyboardInterrupt` and other critical exceptions now propagate correctly.
- **TCP framing safety:** Server enforces a 1 MB maximum message size and
  sanitizes newlines from error strings to prevent JSON framing corruption.

### Features

- **Version single-sourcing:** `julabo_control.__version__` is read from package
  metadata via `importlib.metadata`. CLI `--version` flag displays the correct
  version.
- **Traffic logging:** `--log-traffic` flag on both server and client writes
  timestamped request/response pairs to a file for debugging.
- **Forget cached port:** `julabo forget-port` subcommand removes the cached
  serial port file (`~/.julabo_control_port`).
- **DPI-aware fonts:** Font size auto-scales based on screen DPI. Override with
  `--font-size` on the GUI or remote client.
- **Config validation:** Unknown sections and keys in the INI config file are
  logged as warnings. New `get_int()`, `get_float()`, `get_bool()` helpers
  with range clamping.

### Improvements

- **Serial rate limiting:** 100 ms minimum interval between serial commands
  prevents overwhelming the Julabo hardware.
- **Setpoint verify retry:** `set_setpoint()` retries verification up to 3 times
  with 50 ms delays between attempts before raising an error.
- **Graceful server shutdown:** Signal handler logs active connections and rejects
  new connections during shutdown.
- **Schedule duplicate validation:** `SetpointSchedule.load_csv()` raises
  `ValueError` if duplicate `elapsed_minutes` values are found.
- **Debug logging for silent errors:** `remember_port()`, `read_cached_port()`,
  and `_log_temperature()` now log suppressed errors at DEBUG level.

### Testing

- **Coverage expansion:** GUI, remote client, UI base class, config, and core
  modules received significant new test coverage. New test classes for
  `TestConnection`, `LoadScheduleSuccess`, `OnClose` with logger/schedule,
  alarm/flash callbacks, DPI font detection, config validation, type helpers,
  TCP framing, graceful shutdown, and traffic logging.

## v0.2.0

### Security

- **Fix shell injection in notifications:** macOS osascript now receives the
  script via stdin with proper escaping; Windows PowerShell XML content is
  escaped to prevent injection through title or message strings.
- **Auth token from environment variable:** `JULABO_AUTH_TOKEN` env var and
  `--auth-token-file` flag prevent token exposure in `ps` output.

### Features

- **Temperature logging:** `--temperature-log` flag records every reading to a
  CSV file with UTC timestamps and elapsed minutes.
- **Setpoint schedules:** Load CSV schedule files in the local GUI for automated
  temperature ramps with linear interpolation between steps.
- **Schedule progress indicator:** Status bar shows elapsed/total time and
  percentage during schedule execution.
- **Schedule ramp overlay:** The temperature chart displays the loaded schedule
  as a dashed green line.
- **Desktop notifications:** `--desktop-notifications` flag triggers OS-level
  alerts on temperature alarms (macOS, Linux, Windows).
- **TLS encryption:** `--tls-cert`/`--tls-key` for the server and `--tls` for
  the client enable encrypted connections.
- **Rate limiting:** `--rate-limit` on the server caps requests per IP per
  minute.
- **Protocol versioning:** Server responses include a `protocol_version` field;
  the client logs a warning when the server version is newer.
- **Keyboard shortcuts:** `Ctrl+R` refresh, `Ctrl+S` export CSV, `Escape` close
  (local GUI).
- **Example schedules:** `examples/` directory with ramp, step-and-hold, and
  thermal cycling CSV files.
- **Dockerfile:** Minimal container image for running the server.

### Improvements

- **Buffered temperature logger:** File handle is kept open between writes with
  flush-after-each-row for better I/O efficiency and crash durability. Supports
  context manager protocol.
- **Schedule tick exception guard:** Errors during schedule execution are caught,
  logged, and the schedule is stopped gracefully instead of crashing the poll
  loop.
- **Rate limiter stale IP eviction:** IPs with no recent requests are removed
  from the internal dict to prevent unbounded memory growth.
- **Exponential backoff:** Reconnection attempts in the GUI, remote client, and
  server startup use exponential backoff with configurable limits.

### Breaking changes

- **Python 3.9 minimum:** `requires-python` bumped from `>=3.8` to `>=3.9`;
  ruff target updated to `py39`.

### Testing

- **Integration tests:** Real TCP server + client round-trip tests covering
  authentication, rate limiting, and protocol versioning.
- **Property-based tests:** Hypothesis-powered tests for schedule interpolation
  and CSV round-trip serialization.

## v0.1.0

Initial release of the Julabo Control Suite.

### Features

- **Core library** (`julabo_control.core`): `JulaboChiller` class with serial
  communication, auto-detection, port caching, and setpoint validation
  (range: -50 to 200 °C).
- **Local GUI** (`julabo gui`): Tk-based `ChillerApp` class with live temperature
  plotting, CSV export, configurable poll interval, and alarm threshold.
- **Remote server** (`julabo-server`): TCP JSON server exposing chiller commands with
  optional authentication tokens and exponential backoff on startup retries.
- **Remote client** (`julabo-remote`): Tk GUI that proxies commands through the remote
  server, with authentication support.
- **CLI** (`julabo`): Subcommands for version, status, get/set setpoint, temperature
  reading, start/stop, and raw command passthrough.
- **Configuration file**: Optional `~/.julabo_control.ini` for persistent settings
  across all tools.
- **Temperature alarm**: Audible and visual alarm when temperature deviates beyond a
  configurable threshold.
- **Exponential backoff**: Reconnection attempts in the GUI, remote client retries, and
  server startup all use exponential backoff.
- **PEP 561 compliance**: `py.typed` marker for downstream type-checking.
- **Test suite**: pytest-based tests with coverage reporting.
- **CI**: GitHub Actions workflow with linting (ruff), type checking (mypy), and tests
  across Python 3.9, 3.11, and 3.12.
