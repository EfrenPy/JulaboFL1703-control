"""TCP server that exposes a Julabo chiller over the network."""

from __future__ import annotations

import argparse
import collections
import json
import logging
import os
import signal
import socket
import socketserver
import ssl
import threading
import time
from pathlib import Path
from typing import Any

import serial

from .core import (
    DEFAULT_BAUDRATE,
    DEFAULT_TIMEOUT,
    ChillerBackend,
    JulaboChiller,
    JulaboError,
    SerialSettings,
    auto_detect_port,
)
from .dispatch import _WRITE_COMMANDS, dispatch_command
from .schedule import ScheduleRunner, SetpointSchedule

LOGGER = logging.getLogger(__name__)

PROTOCOL_VERSION = 2
MAX_MESSAGE_SIZE = 1_048_576  # 1 MB

INITIAL_RETRY_DELAY = 5.0
MAX_RETRY_DELAY = 60.0
RETRY_BACKOFF_FACTOR = 2.0

DEFAULT_RATE_LIMIT = 60  # max requests per IP per window
DEFAULT_RATE_WINDOW = 60.0  # seconds

WATCHDOG_INTERVAL = 10.0
WATCHDOG_INITIAL_BACKOFF = 5.0
WATCHDOG_MAX_BACKOFF = 60.0
WATCHDOG_BACKOFF_FACTOR = 2.0
MAX_WATCHDOG_RETRIES = 20


class _RateLimiter:
    """Simple per-IP sliding window rate limiter."""

    def __init__(self, max_requests: int = DEFAULT_RATE_LIMIT, window: float = DEFAULT_RATE_WINDOW):
        self._max = max_requests
        self._window = window
        self._requests: dict[str, collections.deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._requests.get(ip)
            if q is not None:
                # Purge expired entries
                while q and q[0] < now - self._window:
                    q.popleft()
                if not q:
                    # Evict stale entry to prevent unbounded dict growth
                    del self._requests[ip]
                    q = None
            if q is not None and len(q) >= self._max:
                return False
            if q is None:
                q = collections.deque()
                self._requests[ip] = q
            q.append(now)
            return True


class JulaboTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Threaded TCP server that proxies requests to a :class:`JulaboChiller`.

    Optionally wraps the socket in TLS when ``ssl_context`` is provided and
    enforces per-IP rate limiting when ``rate_limit`` is greater than zero.
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        chiller: JulaboChiller,
        auth_token: str | None = None,
        ssl_context: ssl.SSLContext | None = None,
        rate_limit: int = 0,
        read_only: bool = False,
        idle_timeout: float = 0,
    ):
        super().__init__(server_address, JulaboRequestHandler)
        self._chillers: dict[str, ChillerBackend] = {"default": chiller}
        self.auth_token = auth_token
        self._lock = threading.Lock()
        self._ssl_context = ssl_context
        self._rate_limiter: _RateLimiter | None = (
            _RateLimiter(max_requests=rate_limit) if rate_limit > 0 else None
        )
        self._shutting_down = False
        self._active_connections = 0
        self._conn_lock = threading.Lock()
        self._traffic_logger: logging.Logger | None = None
        self._read_only = read_only
        self._idle_timeout = idle_timeout
        self._audit_logger: logging.Logger | None = None
        self._serial_connected = True
        self._watchdog: _SerialWatchdog | None = None
        self._schedule_runners: dict[str, ScheduleRunner] = {}
        self._schedule_threads: dict[str, threading.Thread] = {}
        self._schedule_lock = threading.Lock()
        self._metrics: _MetricsState | None = None
        self._metrics_server: _MetricsHTTPServer | None = None

    @property
    def chiller(self) -> ChillerBackend:
        """Return the default chiller (backward compatible)."""
        return self._chillers["default"]

    @chiller.setter
    def chiller(self, value: ChillerBackend) -> None:
        self._chillers["default"] = value

    def add_chiller(self, chiller_id: str, chiller: ChillerBackend) -> None:
        """Register an additional chiller under *chiller_id*."""
        self._chillers[chiller_id] = chiller

    def _resolve_chiller(self, message: dict[str, Any]) -> ChillerBackend:
        """Look up the chiller by ``chiller_id`` in the message."""
        chiller_id = message.get("chiller_id", "default")
        chiller = self._chillers.get(chiller_id)
        if chiller is None:
            raise ValueError(f"Unknown chiller_id: {chiller_id}")
        return chiller

    def reload_config(self, config_path: str | None = None) -> None:
        """Reload mutable settings from the configuration file.

        Mutable: ``rate_limit``, ``read_only``, ``idle_timeout``.
        Immutable (logs a warning if changed): ``host``, ``port``, ``serial_port``.
        """
        from .config import load_config

        path = Path(config_path) if config_path else None
        try:
            config = load_config(path)
        except Exception as exc:
            LOGGER.warning("Config reload failed: %s", exc)
            return

        server_cfg = config.get("server", {})

        # Warn on immutable changes
        for key in ("host", "port", "serial_port"):
            new_val = server_cfg.get(key)
            if new_val is not None:
                LOGGER.warning(
                    "Config reload: '%s' is immutable and cannot be changed at runtime",
                    key,
                )

        # Apply mutable settings
        with self._lock:
            raw_rate = server_cfg.get("rate_limit")
            if raw_rate is not None:
                rate = int(raw_rate)
                self._rate_limiter = (
                    _RateLimiter(max_requests=rate) if rate > 0 else None
                )
                LOGGER.info("Config reload: rate_limit=%s", rate)

            raw_ro = server_cfg.get("read_only")
            if raw_ro is not None:
                self._read_only = raw_ro.lower() in ("1", "true", "yes")
                LOGGER.info("Config reload: read_only=%s", self._read_only)

            raw_idle = server_cfg.get("idle_timeout")
            if raw_idle is not None:
                self._idle_timeout = float(raw_idle)
                LOGGER.info("Config reload: idle_timeout=%s", self._idle_timeout)

    def begin_shutdown(self) -> None:
        """Mark the server as shutting down and log active connections."""
        with self._conn_lock:
            self._shutting_down = True
            LOGGER.info(
                "Beginning shutdown with %d active connection(s)",
                self._active_connections,
            )

    def increment_connections(self) -> bool:
        """Register a new connection. Returns False if shutting down."""
        with self._conn_lock:
            if self._shutting_down:
                return False
            self._active_connections += 1
            return True

    def decrement_connections(self) -> None:
        """Unregister a connection."""
        with self._conn_lock:
            self._active_connections -= 1

    def get_request(self) -> tuple[Any, Any]:
        """Optionally wrap accepted connections with TLS."""
        conn, addr = super().get_request()
        if self._ssl_context is not None:
            conn = self._ssl_context.wrap_socket(conn, server_side=True)
        return conn, addr

    def process_command(
        self, message: dict[str, Any], client_ip: str = ""
    ) -> dict[str, Any]:
        if self.auth_token is not None:
            token = message.get("token")
            if token != self.auth_token:
                raise PermissionError("Invalid or missing authentication token")

        command = message.get("command")
        if not command:
            raise ValueError("Missing 'command' in request payload")

        _start = time.monotonic()

        # Read-only mode blocks write commands (including schedule mutations)
        _SCHEDULE_WRITE = {"load_schedule", "stop_schedule"}
        if self._read_only and (command in _WRITE_COMMANDS or command in _SCHEDULE_WRITE):
            return {"status": "error", "error": "Server is in read-only mode"}

        # Fast path: ping needs no chiller or serial
        if command == "ping":
            _elapsed = time.monotonic() - _start
            LOGGER.debug(
                "Processed command %s in %.3fs", command, _elapsed,
                extra={"client_ip": client_ip, "command": command, "latency": _elapsed},
            )
            if self._metrics is not None:
                self._metrics.record_command(command, _elapsed)
            return {"status": "ok", "result": "pong", "protocol_version": PROTOCOL_VERSION}

        # Serial disconnected — block everything except ping (handled above)
        if not self._serial_connected:
            return {
                "status": "error",
                "error": "Serial connection lost, reconnecting...",
            }

        result: Any
        chiller = self._resolve_chiller(message)
        with self._lock:
            # Capture old values for audit logging on write commands
            old_value: str | None = None
            if self._audit_logger is not None and command in _WRITE_COMMANDS:
                try:
                    if command == "set_setpoint":
                        old_value = str(chiller.get_setpoint())
                    elif command in ("start", "stop", "set_running"):
                        old_value = str(chiller.is_running())
                except Exception:
                    old_value = "unknown"

            if command == "load_schedule":
                csv_data = message.get("csv")
                if not csv_data:
                    raise ValueError("'load_schedule' requires a 'csv' string")
                sched_chiller_id = message.get("chiller_id", "default")
                result = self._load_schedule(csv_data, chiller_id=sched_chiller_id)
            elif command == "stop_schedule":
                sched_chiller_id = message.get("chiller_id", "default")
                result = self._stop_schedule(chiller_id=sched_chiller_id)
            elif command == "schedule_status":
                sched_chiller_id = message.get("chiller_id", "default")
                result = self._get_schedule_status(chiller_id=sched_chiller_id)
            else:
                result = dispatch_command(chiller, command, message)

            # Audit log for write commands
            if self._audit_logger is not None and command in _WRITE_COMMANDS:
                self._audit(command, client_ip, result, old_value)

        # Structured logging
        _elapsed = time.monotonic() - _start
        LOGGER.debug(
            "Processed command %s in %.3fs",
            command,
            _elapsed,
            extra={"client_ip": client_ip, "command": command, "latency": _elapsed},
        )

        # Metrics
        if self._metrics is not None:
            self._metrics.record_command(command, _elapsed)
            if command in ("status_all", "temperature") and isinstance(result, dict):
                self._metrics.cache_status(result)

        return {"status": "ok", "result": result, "protocol_version": PROTOCOL_VERSION}

    def _audit(
        self,
        command: str,
        client_ip: str,
        result: Any,
        old_value: str | None,
    ) -> None:
        """Write an audit log entry for a write command."""
        if self._audit_logger is None:
            return
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if command == "set_setpoint":
            self._audit_logger.info(
                "%s %s set_setpoint %s (was %s)", ts, client_ip, result, old_value
            )
        elif command in ("start", "stop"):
            self._audit_logger.info("%s %s %s", ts, client_ip, command)
        elif command == "set_running":
            self._audit_logger.info(
                "%s %s set_running %s (was %s)", ts, client_ip, result, old_value
            )

    # -- Schedule support --

    def _schedule_apply_setpoint(self, chiller_id: str, value: float) -> None:
        """Callback for the schedule runner, acquires _lock for serial."""
        chiller = self._chillers.get(chiller_id)
        if chiller is None:
            return
        with self._lock:
            chiller.set_setpoint(value)

    def _schedule_ticker(self, chiller_id: str) -> None:
        """Background thread that ticks the schedule runner for a specific chiller."""
        while True:
            with self._schedule_lock:
                runner = self._schedule_runners.get(chiller_id)
            if runner is None or not runner.is_running:
                return
            try:
                runner.tick()
            except Exception as exc:
                LOGGER.error("Schedule tick error for %s: %s", chiller_id, exc)
                with self._schedule_lock:
                    if self._schedule_runners.get(chiller_id) is runner:
                        runner.stop()
                        del self._schedule_runners[chiller_id]
                return
            if runner.is_finished:
                with self._schedule_lock:
                    if self._schedule_runners.get(chiller_id) is runner:
                        del self._schedule_runners[chiller_id]
                return
            time.sleep(2.0)

    def _load_schedule(
        self, csv_data: str, chiller_id: str = "default"
    ) -> dict[str, Any]:
        import functools

        schedule = SetpointSchedule.from_csv_string(csv_data)
        with self._schedule_lock:
            existing = self._schedule_runners.get(chiller_id)
            if existing is not None:
                existing.stop()
            apply_fn = functools.partial(self._schedule_apply_setpoint, chiller_id)
            runner = ScheduleRunner(schedule, apply_fn)
            runner.start()
            self._schedule_runners[chiller_id] = runner
        thread = threading.Thread(
            target=self._schedule_ticker, args=(chiller_id,), daemon=True
        )
        self._schedule_threads[chiller_id] = thread
        thread.start()
        return {
            "steps": len(schedule.steps),
            "duration_minutes": schedule.duration_minutes,
        }

    def _stop_schedule(self, chiller_id: str = "default") -> str:
        with self._schedule_lock:
            runner = self._schedule_runners.get(chiller_id)
            if runner is not None:
                runner.stop()
                del self._schedule_runners[chiller_id]
        return "stopped"

    def _get_schedule_status(self, chiller_id: str = "default") -> dict[str, Any]:
        with self._schedule_lock:
            runner = self._schedule_runners.get(chiller_id)
        if runner is None or not runner.is_running:
            return {"running": False}
        elapsed = runner.elapsed_minutes
        total = runner.schedule.duration_minutes
        target = runner.schedule.setpoint_at(elapsed)
        pct = min(100.0, elapsed / total * 100) if total > 0 else 100.0
        return {
            "running": True,
            "elapsed_minutes": round(elapsed, 1),
            "total_minutes": round(total, 1),
            "current_target": round(target, 2),
            "progress_pct": round(pct, 1),
        }


class JulaboRequestHandler(socketserver.StreamRequestHandler):
    """Handle a single TCP connection."""

    server: JulaboTCPServer  # type: ignore[assignment]

    def setup(self) -> None:
        super().setup()
        if self.server._idle_timeout > 0:
            self.connection.settimeout(self.server._idle_timeout)

    def handle(self) -> None:  # pragma: no cover - network side effects
        client_ip = self.client_address[0]
        if not self.server.increment_connections():
            return
        try:
            self._handle_loop(client_ip)
        finally:
            self.server.decrement_connections()

    def _handle_loop(self, client_ip: str) -> None:  # pragma: no cover - network
        while True:
            try:
                raw = self.rfile.readline(MAX_MESSAGE_SIZE + 1)
            except socket.timeout:
                LOGGER.info("Idle timeout for %s", client_ip)
                return
            except OSError:
                return
            if not raw:
                break
            if len(raw) > MAX_MESSAGE_SIZE:
                response = {"status": "error", "error": "Message too large"}
                LOGGER.warning("Oversized message from %s (%d bytes)", client_ip, len(raw))
                data = json.dumps(response).encode("utf-8") + b"\n"
                self.wfile.write(data)
                continue
            raw = raw.strip()
            if not raw:
                continue

            if (
                self.server._rate_limiter is not None
                and not self.server._rate_limiter.allow(client_ip)
            ):
                response = {"status": "error", "error": "Rate limit exceeded"}
                LOGGER.warning("Rate limit exceeded for %s", client_ip)
            else:
                try:
                    message = json.loads(raw.decode("utf-8"))
                    response = self.server.process_command(message, client_ip=client_ip)
                except (
                    json.JSONDecodeError, ValueError, TypeError,
                    PermissionError, JulaboError, TimeoutError,
                    serial.SerialException,
                ) as exc:
                    LOGGER.exception("Failed to process message: %s", raw)
                    error_msg = _sanitize_error(exc)
                    response = {"status": "error", "error": error_msg}
                    if self.server._metrics is not None:
                        self.server._metrics.record_error(type(exc).__name__)

            if self.server._traffic_logger is not None:
                self.server._traffic_logger.debug(
                    "REQ %s %s", client_ip, raw.decode("utf-8", errors="replace")
                )
                self.server._traffic_logger.debug(
                    "RES %s %s", client_ip, json.dumps(response)
                )

            data = json.dumps(response).encode("utf-8") + b"\n"
            self.wfile.write(data)


_HISTOGRAM_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_MAX_LATENCY_SAMPLES = 1000


class _MetricsState:
    """Thread-safe metrics accumulator."""

    def __init__(self) -> None:
        self.commands_total: dict[str, int] = {}
        self.errors_total: dict[str, int] = {}
        self.last_temperature: float | None = None
        self.last_setpoint: float | None = None
        self.last_running: bool | None = None
        self.command_latencies: list[float] = []
        self.lock = threading.Lock()

    def record_command(self, command: str, latency: float) -> None:
        with self.lock:
            self.commands_total[command] = self.commands_total.get(command, 0) + 1
            self.command_latencies.append(latency)
            if len(self.command_latencies) > _MAX_LATENCY_SAMPLES:
                self.command_latencies = self.command_latencies[-_MAX_LATENCY_SAMPLES:]

    def record_error(self, error_type: str) -> None:
        with self.lock:
            self.errors_total[error_type] = self.errors_total.get(error_type, 0) + 1

    def cache_status(self, result: dict[str, Any]) -> None:
        with self.lock:
            if "temperature" in result:
                self.last_temperature = float(result["temperature"])
            if "setpoint" in result:
                self.last_setpoint = float(result["setpoint"])
            if "is_running" in result:
                self.last_running = bool(result["is_running"])

    def render_prometheus(self) -> str:
        with self.lock:
            lines: list[str] = []
            # Gauges
            if self.last_temperature is not None:
                lines.append(
                    f"julabo_temperature_celsius {self.last_temperature}"
                )
            if self.last_setpoint is not None:
                lines.append(f"julabo_setpoint_celsius {self.last_setpoint}")
            if self.last_running is not None:
                lines.append(
                    f"julabo_pump_running {1 if self.last_running else 0}"
                )
            # Counters
            for cmd, count in sorted(self.commands_total.items()):
                lines.append(
                    f'julabo_commands_total{{command="{cmd}"}} {count}'
                )
            for err_type, count in sorted(self.errors_total.items()):
                lines.append(
                    f'julabo_commands_errors_total{{type="{err_type}"}} {count}'
                )
            # Histogram
            if self.command_latencies:
                sorted_lat = sorted(self.command_latencies)
                total = len(sorted_lat)
                lat_sum = sum(sorted_lat)
                idx = 0
                for bucket in _HISTOGRAM_BUCKETS:
                    while idx < total and sorted_lat[idx] <= bucket:
                        idx += 1
                    lines.append(
                        f'julabo_command_latency_seconds_bucket{{le="{bucket}"}} {idx}'
                    )
                lines.append(
                    f'julabo_command_latency_seconds_bucket{{le="+Inf"}} {total}'
                )
                lines.append(f"julabo_command_latency_seconds_count {total}")
                lines.append(f"julabo_command_latency_seconds_sum {lat_sum:.6f}")
            lines.append("")
            return "\n".join(lines)


class _MetricsHTTPServer:
    """HTTP server that serves Prometheus metrics."""

    def __init__(
        self,
        address: tuple[str, int],
        metrics: _MetricsState,
    ) -> None:
        from http.server import BaseHTTPRequestHandler, HTTPServer

        state = metrics

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/metrics":
                    body = state.render_prometheus().encode("utf-8")
                    self.send_response(200)
                    self.send_header(
                        "Content-Type",
                        "text/plain; version=0.0.4; charset=utf-8",
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

            def log_message(self, fmt: str, *args: Any) -> None:
                LOGGER.debug(fmt, *args)

        self._server = HTTPServer(address, Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5.0)


class _SerialWatchdog:
    """Daemon thread that periodically checks serial health and reconnects."""

    def __init__(self, server: JulaboTCPServer) -> None:
        self._server = server
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=5.0)

    def _run(self) -> None:
        while not self._stop_event.wait(WATCHDOG_INTERVAL):
            try:
                with self._server._lock:
                    self._server.chiller.get_status()
            except Exception:
                LOGGER.warning("Serial watchdog detected failure, attempting reconnect")
                with self._server._lock:
                    self._server._serial_connected = False
                try:
                    with self._server._lock:
                        self._server.chiller.close()
                except Exception:
                    pass
                self._reconnect()

    def _reconnect(self) -> None:
        backoff = WATCHDOG_INITIAL_BACKOFF
        retries = 0
        while not self._stop_event.is_set():
            if self._stop_event.wait(backoff):
                return
            retries += 1
            if retries > MAX_WATCHDOG_RETRIES:
                LOGGER.critical(
                    "Serial watchdog giving up after %d retries", retries - 1
                )
                return
            try:
                with self._server._lock:
                    self._server.chiller.connect()
                    self._server.chiller.identify()
                    self._server._serial_connected = True
                LOGGER.info("Serial watchdog reconnected successfully")
                return
            except Exception:
                LOGGER.debug("Watchdog reconnect failed, backoff=%.1f", backoff)
                backoff = min(backoff * WATCHDOG_BACKOFF_FACTOR, WATCHDOG_MAX_BACKOFF)


def _sanitize_error(exc: Exception) -> str:
    """Return a safe error message for clients, hiding internal details."""
    if isinstance(exc, PermissionError):
        return "Authentication failed"
    if isinstance(exc, ValueError):
        return f"Invalid request: {exc}"
    if isinstance(exc, TypeError):
        return "Invalid argument type"
    if isinstance(exc, TimeoutError):
        return "Device timeout"
    if isinstance(exc, JulaboError):
        return f"Device error: {exc}"
    return "Internal server error"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "serial_port",
        nargs="?",
        help=(
            "Serial port path for the Julabo chiller. If omitted the server tries "
            "to auto-detect the adapter."
        ),
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Host interface to bind the TCP server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=None, help="TCP port number to listen on (default: 8765)"
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=None,
        help="Serial baudrate (default matches Julabo requirements)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Serial read timeout in seconds",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Require clients to provide this token for authentication",
    )
    parser.add_argument(
        "--auth-token-file",
        default=None,
        help="Path to a file containing the auth token (one line, stripped)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging output",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Path to a log file (in addition to console output)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to configuration file (default: ~/.julabo_control.ini)",
    )
    parser.add_argument(
        "--tls-cert",
        default=None,
        help="Path to TLS certificate file for encrypted connections",
    )
    parser.add_argument(
        "--tls-key",
        default=None,
        help="Path to TLS private key file",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=None,
        help="Max requests per IP per minute (0 to disable, default: 0)",
    )
    parser.add_argument(
        "--log-traffic",
        default=None,
        help="Path to a file for logging TCP request/response pairs",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        default=False,
        help="Reject write commands (set_setpoint, start, stop, set_running)",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=None,
        help="Disconnect idle clients after this many seconds (0=disabled, default: 0)",
    )
    parser.add_argument(
        "--audit-log",
        default=None,
        help="Path to an audit log file for write command tracking",
    )
    parser.add_argument(
        "--no-watchdog",
        action="store_true",
        default=False,
        help="Disable the serial watchdog/auto-reconnect thread",
    )
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=None,
        help="Port for Prometheus metrics HTTP endpoint (disabled if omitted)",
    )
    parser.add_argument(
        "--log-format",
        choices=["text", "json"],
        default=None,
        help="Log output format (default: text)",
    )
    return parser.parse_args()


def resolve_auth_token(
    cli_token: str | None,
    cli_token_file: str | None,
    config_token: str | None,
) -> str | None:
    """Determine the auth token with priority: CLI > file > env > config."""
    if cli_token:
        return cli_token
    if cli_token_file:
        return Path(cli_token_file).read_text().strip()
    env_token = os.environ.get("JULABO_AUTH_TOKEN")
    if env_token:
        return env_token
    return config_token or None


def configure_logging(
    verbose: bool,
    log_file: str | None = None,
    log_format: str = "text",
) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    if log_format == "json":
        from .logging_utils import JsonFormatter

        json_fmt = JsonFormatter()
        for h in handlers:
            h.setFormatter(json_fmt)
    else:
        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        text_fmt = logging.Formatter(fmt)
        for h in handlers:
            h.setFormatter(text_fmt)

    logging.basicConfig(level=level, handlers=handlers)


def _setup_logging(
    args: argparse.Namespace,
    config: dict[str, Any],
) -> None:
    """Configure logging from CLI args and config."""
    configure_logging(
        args.verbose,
        getattr(args, "log_file", None),
        log_format=getattr(args, "log_format", None) or "text",
    )


def _connect_serial(
    args: argparse.Namespace,
    config: dict[str, Any],
) -> JulaboChiller:
    """Resolve the serial port and connect to the chiller with retry."""
    server_cfg = config.get("server", {})
    baudrate = (
        args.baudrate
        if args.baudrate is not None
        else int(server_cfg.get("baudrate", str(DEFAULT_BAUDRATE)))
    )
    timeout = (
        args.timeout
        if args.timeout is not None
        else float(server_cfg.get("timeout", str(DEFAULT_TIMEOUT)))
    )

    retry_delay = INITIAL_RETRY_DELAY
    while True:
        serial_port = args.serial_port or server_cfg.get("serial_port")
        if serial_port:
            LOGGER.info("Using configured Julabo serial port %s", serial_port)
        else:
            try:
                serial_port = auto_detect_port(timeout)
            except serial.SerialException as exc:
                LOGGER.warning(
                    "Unable to locate Julabo chiller automatically: %s. "
                    "Retrying in %.1f seconds...",
                    exc,
                    retry_delay,
                )
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * RETRY_BACKOFF_FACTOR, MAX_RETRY_DELAY)
                continue
            LOGGER.info("Auto-detected Julabo serial port at %s", serial_port)

        settings = SerialSettings(
            port=serial_port,
            baudrate=baudrate,
            timeout=timeout,
        )

        chiller = JulaboChiller(settings)
        try:
            chiller.connect()
            chiller.identify()
        except (JulaboError, TimeoutError, serial.SerialException) as exc:
            chiller.close()
            LOGGER.error("Failed to connect to Julabo on %s: %s", serial_port, exc)
            if args.serial_port:
                raise SystemExit(2) from exc
            LOGGER.info("Will retry detection in %.1f seconds", retry_delay)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * RETRY_BACKOFF_FACTOR, MAX_RETRY_DELAY)
            continue

        return chiller


def _create_server(
    args: argparse.Namespace,
    config: dict[str, Any],
    chiller: JulaboChiller,
    auth_token: str | None,
) -> JulaboTCPServer:
    """Build and configure a :class:`JulaboTCPServer` from CLI args and config."""
    server_cfg = config.get("server", {})
    host = args.host or server_cfg.get("host", "127.0.0.1")
    port = args.port if args.port is not None else int(server_cfg.get("port", "8765"))

    # TLS support
    tls_cert = getattr(args, "tls_cert", None) or server_cfg.get("tls_cert")
    tls_key = getattr(args, "tls_key", None) or server_cfg.get("tls_key")
    ssl_context: ssl.SSLContext | None = None
    if tls_cert:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(tls_cert, keyfile=tls_key)
        LOGGER.info("TLS enabled with certificate %s", tls_cert)

    # Rate limiting
    raw_rate = (
        args.rate_limit if getattr(args, "rate_limit", None) is not None
        else int(server_cfg.get("rate_limit", "0"))
    )

    # Read-only mode
    read_only = getattr(args, "read_only", False) or (
        server_cfg.get("read_only", "").lower() in ("1", "true", "yes")
    )

    # Idle timeout
    idle_timeout = (
        args.idle_timeout if getattr(args, "idle_timeout", None) is not None
        else float(server_cfg.get("idle_timeout", "0"))
    )

    server = JulaboTCPServer(
        (host, port), chiller, auth_token=auth_token,
        ssl_context=ssl_context, rate_limit=raw_rate,
        read_only=read_only, idle_timeout=idle_timeout,
    )

    # Traffic logging
    traffic_file = getattr(args, "log_traffic", None) or server_cfg.get("log_traffic")
    if traffic_file:
        traffic_logger = logging.getLogger("julabo_control.traffic")
        traffic_handler = logging.FileHandler(traffic_file)
        traffic_handler.setFormatter(
            logging.Formatter("%(asctime)s %(message)s")
        )
        traffic_logger.addHandler(traffic_handler)
        traffic_logger.setLevel(logging.DEBUG)
        server._traffic_logger = traffic_logger
        LOGGER.info("Traffic logging enabled: %s", traffic_file)

    # Audit logging
    audit_file = getattr(args, "audit_log", None) or server_cfg.get("audit_log")
    if audit_file:
        audit_logger = logging.getLogger("julabo_control.audit")
        audit_handler = logging.FileHandler(audit_file)
        audit_handler.setFormatter(logging.Formatter("%(message)s"))
        audit_logger.addHandler(audit_handler)
        audit_logger.setLevel(logging.INFO)
        server._audit_logger = audit_logger
        LOGGER.info("Audit logging enabled: %s", audit_file)

    # Serial watchdog
    no_watchdog = getattr(args, "no_watchdog", False) or (
        server_cfg.get("watchdog", "").lower() in ("0", "false", "no")
    )
    if not no_watchdog:
        watchdog = _SerialWatchdog(server)
        server._watchdog = watchdog
        watchdog.start()
        LOGGER.info("Serial watchdog enabled")

    # Metrics
    server._metrics = _MetricsState()
    metrics_port = (
        getattr(args, "metrics_port", None)
        or int(server_cfg.get("metrics_port", "0"))
    )
    if metrics_port:
        metrics_srv = _MetricsHTTPServer(("", metrics_port), server._metrics)
        server._metrics_server = metrics_srv
        metrics_srv.start()
        LOGGER.info("Metrics HTTP server on port %d", metrics_port)

    LOGGER.info("Listening on %s:%s", host, port)
    if auth_token:
        LOGGER.info("Authentication enabled")
    if raw_rate > 0:
        LOGGER.info("Rate limiting: %d requests/IP/min", raw_rate)
    if read_only:
        LOGGER.info("Read-only mode enabled")

    return server


def main() -> None:  # pragma: no cover - CLI helper
    from .config import load_config

    args = parse_arguments()

    config_path = Path(args.config) if args.config else None
    config = load_config(config_path)

    _setup_logging(args, config)

    server_cfg = config.get("server", {})
    auth_token = resolve_auth_token(
        args.auth_token,
        getattr(args, "auth_token_file", None),
        server_cfg.get("auth_token"),
    )

    chiller = _connect_serial(args, config)
    server = _create_server(args, config, chiller, auth_token)

    def handle_signal(signum: int, _frame: Any) -> None:  # pragma: no cover - signal handler
        LOGGER.info("Received signal %s, shutting down.", signum)
        server.begin_shutdown()
        server.shutdown()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if hasattr(signal, "SIGHUP"):  # pragma: no cover - Unix only
        def handle_sighup(signum: int, _frame: Any) -> None:
            LOGGER.info("Received SIGHUP, reloading configuration.")
            server.reload_config(args.config)

        signal.signal(signal.SIGHUP, handle_sighup)

    try:
        server.serve_forever()
    finally:
        LOGGER.info("Closing server")
        if server._watchdog is not None:
            server._watchdog.stop()
        for t in server._schedule_threads.values():
            t.join(timeout=2.0)
        if server._metrics_server is not None:
            server._metrics_server.stop()
        server.server_close()
        chiller.close()


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
