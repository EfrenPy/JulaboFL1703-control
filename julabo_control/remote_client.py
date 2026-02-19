"""GUI client for remotely controlling a Julabo chiller."""

from __future__ import annotations

import argparse
import json
import logging
import socket
import ssl
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Any

from .alarm import TemperatureAlarm
from .core import SETPOINT_MAX, SETPOINT_MIN
from .remote_server import resolve_auth_token
from .temperature_logger import TemperatureFileLogger
from .ui import BaseChillerApp, TemperatureHistoryPlot, configure_default_fonts

LOGGER = logging.getLogger(__name__)

CLIENT_PROTOCOL_VERSION = 2


class _PersistentConnection:
    """Thread-safe persistent TCP connection with auto-reconnect."""

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float,
        ssl_context: ssl.SSLContext | None,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._ssl_context = ssl_context
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._file: Any = None

    def _connect(self) -> None:
        raw = socket.create_connection((self._host, self._port), timeout=self._timeout)
        if self._ssl_context is not None:
            raw = self._ssl_context.wrap_socket(raw, server_hostname=self._host)
        self._sock = raw
        self._file = raw.makefile("rb")

    def send_recv(self, data: bytes) -> bytes:
        with self._lock:
            for attempt in range(2):
                try:
                    if self._sock is None:
                        self._connect()
                    assert self._sock is not None
                    self._sock.sendall(data)
                    assert self._file is not None
                    line = self._file.readline()
                    if not line:
                        raise ConnectionError("No response")
                    return line  # type: ignore[no-any-return]
                except (ConnectionError, OSError, TimeoutError):
                    self._close_inner()
                    if attempt == 1:
                        raise
            raise ConnectionError("send_recv exhausted")

    def _close_inner(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            self._file = None

    def close(self) -> None:
        with self._lock:
            self._close_inner()


class RemoteChillerClient:
    """Minimal TCP client for communicating with the remote control server."""

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = 5.0,
        retries: int = 3,
        auth_token: str | None = None,
        ssl_context: ssl.SSLContext | None = None,
        persistent: bool = False,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.auth_token = auth_token
        self._ssl_context = ssl_context
        self._persistent: _PersistentConnection | None = None
        if persistent:
            self._persistent = _PersistentConnection(host, port, timeout, ssl_context)

    def close(self) -> None:
        """Close the persistent connection if one exists."""
        if self._persistent is not None:
            self._persistent.close()

    def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.auth_token:
            payload["token"] = self.auth_token
        data = json.dumps(payload).encode("utf-8") + b"\n"

        if self._persistent is not None:
            line = self._persistent.send_recv(data)
            response: dict[str, Any] = json.loads(line.decode("utf-8"))
            if response.get("status") != "ok":
                raise RuntimeError(response.get("error", "Unknown error"))
            return response

        last_exc: Exception | None = None
        delay = 0.5
        for attempt in range(self.retries):
            try:
                raw_sock = socket.create_connection(
                    (self.host, self.port), timeout=self.timeout
                )
                sock: socket.socket = raw_sock
                if self._ssl_context is not None:
                    sock = self._ssl_context.wrap_socket(sock, server_hostname=self.host)
                with sock:
                    sock.sendall(data)
                    file = sock.makefile("rb")
                    line = file.readline()
                if not line:
                    raise ConnectionError("No response from server")
                resp: dict[str, Any] = json.loads(line.decode("utf-8"))
                if resp.get("status") != "ok":
                    raise RuntimeError(resp.get("error", "Unknown error"))
                server_version = resp.get("protocol_version")
                if server_version is not None and server_version > CLIENT_PROTOCOL_VERSION:
                    LOGGER.warning(
                        "Server protocol version %s is newer than client version %s",
                        server_version,
                        CLIENT_PROTOCOL_VERSION,
                    )
                return resp
            except (ConnectionError, OSError, TimeoutError) as exc:
                last_exc = exc
                LOGGER.debug(
                    "Send attempt %d/%d failed: %s",
                    attempt + 1,
                    self.retries,
                    exc,
                )
                if attempt < self.retries - 1:
                    import time

                    time.sleep(delay)
                    delay = min(delay * 2, 4.0)
        if last_exc is None:
            raise RuntimeError("All retries exhausted with no recorded exception")
        raise last_exc

    def command(self, name: str, value: Any | None = None) -> Any:
        payload: dict[str, Any] = {"command": name}
        if value is not None:
            payload["value"] = value
        response = self._send(payload)
        return response.get("result")

    def status_all(self) -> dict[str, Any]:
        """Fetch status, temperature, setpoint, and running state in one call."""
        result = self.command("status_all")
        if not isinstance(result, dict):
            raise TypeError(f"Expected dict from status_all, got {type(result).__name__}")
        return result

    def load_schedule(self, csv_data: str) -> dict[str, Any]:
        """Upload a CSV schedule to the server."""
        response = self._send({"command": "load_schedule", "csv": csv_data})
        result = response["result"]
        if not isinstance(result, dict):
            raise TypeError(f"Expected dict from load_schedule, got {type(result).__name__}")
        return result

    def stop_schedule(self) -> str:
        """Stop any running schedule on the server."""
        result = self.command("stop_schedule")
        if not isinstance(result, str):
            raise TypeError(f"Expected str from stop_schedule, got {type(result).__name__}")
        return result

    def schedule_status(self) -> dict[str, Any]:
        """Query the current schedule status."""
        result = self.command("schedule_status")
        if not isinstance(result, dict):
            raise TypeError(f"Expected dict from schedule_status, got {type(result).__name__}")
        return result


class RemoteChillerApp(BaseChillerApp):
    """Tk application that visualises and proxies remote chiller commands."""

    def __init__(
        self,
        root: tk.Tk,
        client: RemoteChillerClient,
        *,
        poll_interval: int = 5000,
        alarm_threshold: float = 2.0,
        log_file: str | None = None,
        desktop_notifications: bool = False,
    ):
        self.root = root
        self.client = client
        self._refresh_job: str | None = None
        self._flash_job: str | None = None

        configure_default_fonts()

        root.title("Julabo Remote Control")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="--")
        self.temperature_var = tk.StringVar(value="-- \u00b0C")
        self.setpoint_var = tk.StringVar(value="-- \u00b0C")
        self.running_var = tk.BooleanVar(value=False)
        self.running_text_var = tk.StringVar(value="Stopped")
        self.new_setpoint_var = tk.StringVar()
        self.poll_interval_var = tk.IntVar(value=poll_interval)
        self.alarm_threshold_var = tk.DoubleVar(value=alarm_threshold)

        self.temperature_plot: TemperatureHistoryPlot | None = None
        self.temperature_logger: TemperatureFileLogger | None = (
            TemperatureFileLogger(log_file) if log_file else None
        )

        self.alarm = TemperatureAlarm(
            threshold=alarm_threshold,
            on_alarm=self._on_alarm,
            on_clear=self._on_clear,
            desktop_notifications=desktop_notifications,
        )

        self._build_layout()
        self._bind_shortcuts()
        self._show_status("", color="black")
        self.refresh_status()
        self._schedule_auto_refresh()

    def _build_layout(self) -> None:
        self.main_frame = frame = tk.Frame(self.root, padx=20, pady=20)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(8, weight=1)

        tk.Label(frame, text="Status:").grid(row=0, column=0, sticky="w")
        tk.Label(frame, textvariable=self.status_var, width=20).grid(
            row=0, column=1, sticky="w"
        )

        tk.Label(frame, text="Temperature (\u00b0C):").grid(row=1, column=0, sticky="w")
        self.temp_label = tk.Label(frame, textvariable=self.temperature_var, width=20)
        self.temp_label.grid(row=1, column=1, sticky="w")

        tk.Label(frame, text="Setpoint (\u00b0C):").grid(row=2, column=0, sticky="w")
        tk.Label(frame, textvariable=self.setpoint_var, width=20).grid(
            row=2, column=1, sticky="w"
        )

        tk.Label(frame, text="Machine state:").grid(row=3, column=0, sticky="w")
        tk.Label(frame, textvariable=self.running_text_var, width=20).grid(
            row=3, column=1, sticky="w"
        )

        control_frame = tk.LabelFrame(frame, text="Controls", padx=10, pady=10)
        control_frame.grid(row=4, column=0, columnspan=2, pady=(10, 0), sticky="ew")

        tk.Button(control_frame, text="Refresh", command=self.refresh_status).grid(
            row=0, column=0, padx=5
        )
        self.toggle_button = tk.Button(
            control_frame, text="Start machine", command=self._toggle_running
        )
        self.toggle_button.grid(row=0, column=1, padx=5)
        tk.Button(control_frame, text="Export CSV", command=self.export_csv).grid(
            row=0, column=2, padx=5
        )

        tk.Label(control_frame, text="Set new setpoint:").grid(
            row=1, column=0, sticky="e", pady=(10, 0)
        )
        setpoint_entry = tk.Entry(
            control_frame, textvariable=self.new_setpoint_var, width=10
        )
        setpoint_entry.grid(row=1, column=1, pady=(10, 0))
        tk.Button(control_frame, text="Apply", command=self._apply_setpoint).grid(
            row=1, column=2, padx=5, pady=(10, 0)
        )

        # Poll interval
        tk.Label(frame, text="Poll interval (ms):").grid(row=5, column=0, sticky="w")
        tk.Spinbox(
            frame,
            from_=1000,
            to=60000,
            increment=1000,
            textvariable=self.poll_interval_var,
            width=8,
        ).grid(row=5, column=1, sticky="w")

        # Alarm threshold
        tk.Label(frame, text="Alarm threshold (\u00b0C):").grid(
            row=6, column=0, sticky="w"
        )
        tk.Spinbox(
            frame,
            from_=0.0,
            to=20.0,
            increment=0.5,
            textvariable=self.alarm_threshold_var,
            width=8,
            format="%.1f",
        ).grid(row=6, column=1, sticky="w")

        self.status_label = tk.Label(frame, textvariable=tk.StringVar(value=""), fg="black")
        self.status_label.grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )
        # Reuse status_var for status_label text
        self._msg_var = tk.StringVar(value="")
        self.status_label.configure(textvariable=self._msg_var)

        plot_frame = tk.LabelFrame(
            frame, text="Temperature Trend", padx=10, pady=10
        )
        plot_frame.grid(row=8, column=0, columnspan=2, sticky="nsew", pady=(10, 0))

        self.temperature_plot = TemperatureHistoryPlot(plot_frame)
        self.temperature_plot.widget.pack(fill="both", expand=True)
        self.temperature_plot.clear()

        for child in frame.winfo_children():
            child.grid_configure(padx=4, pady=4)  # type: ignore[union-attr]

    def _bind_shortcuts(self) -> None:
        """Register keyboard shortcuts on the root window."""
        self.root.bind("<Control-r>", lambda _e: self.refresh_status())
        self.root.bind("<Control-s>", lambda _e: self.export_csv())

    def _show_status(self, message: str, *, color: str = "red") -> None:
        self._msg_var.set(message)
        self.status_label.configure(fg=color)

    def _update_running_state(self) -> None:
        running = self.running_var.get()
        self.running_text_var.set("Running" if running else "Stopped")
        self._update_running_button()

    def _toggle_running(self) -> None:
        target_state = not self.running_var.get()
        command = "start" if target_state else "stop"
        try:
            confirmed = bool(self.client.command(command))
        except (RuntimeError, OSError, ConnectionError, TimeoutError) as exc:
            messagebox.showerror("Machine control error", str(exc))
            self._show_status(f"Error: {exc}")
            return

        self.refresh_status(clear_message=False)
        self._show_status(
            "Machine started" if confirmed else "Machine stopped",
            color="green" if confirmed else "red",
        )

    def _apply_setpoint(self) -> None:
        try:
            value = float(self.new_setpoint_var.get())
        except ValueError:
            messagebox.showwarning("Invalid input", "Please enter a numeric value.")
            return

        if not (SETPOINT_MIN <= value <= SETPOINT_MAX):
            messagebox.showwarning(
                "Out of range",
                f"Setpoint must be between {SETPOINT_MIN} and {SETPOINT_MAX} \u00b0C.",
            )
            return

        try:
            confirmed = float(self.client.command("set_setpoint", value))
        except (RuntimeError, OSError, ConnectionError, TimeoutError, ValueError) as exc:
            messagebox.showerror("Setpoint error", str(exc))
            self._show_status(f"Error: {exc}")
            return

        self.new_setpoint_var.set("")
        self.refresh_status(clear_message=False)
        self._show_status(
            f"Setpoint updated to {confirmed:.2f} \u00b0C",
            color="green",
        )

    def _schedule_auto_refresh(self) -> None:
        def _auto_refresh() -> None:
            self._auto_refresh_status()
            if self.root.winfo_exists():
                self._refresh_job = self.root.after(
                    self.poll_interval_var.get(), _auto_refresh
                )

        self._refresh_job = self.root.after(
            self.poll_interval_var.get(), _auto_refresh
        )

    def _apply_status_data(
        self, status: str, temperature: float, setpoint: float, running: bool
    ) -> None:
        """Update all UI elements from polled data."""
        self.status_var.set(status)
        self.temperature_var.set(f"{temperature:.2f} \u00b0C")
        self.setpoint_var.set(f"{setpoint:.2f} \u00b0C")
        self.running_var.set(running)
        self._update_running_state()
        self._update_temperature_plot(temperature)
        if self.temperature_plot is not None:
            self.temperature_plot.set_setpoint(setpoint)
        self._log_temperature(temperature, setpoint)
        self.alarm.threshold = self.alarm_threshold_var.get()
        self.alarm.check(temperature, setpoint)

    def _auto_refresh_status(self) -> None:
        """Non-blocking refresh (no messagebox on error)."""
        try:
            data = self.client.status_all()
        except (RuntimeError, OSError, ConnectionError, TimeoutError) as exc:
            LOGGER.error("Auto-refresh failed: %s", exc)
            self._show_status(f"Error: {exc}")
            return

        self._apply_status_data(
            data["status"],
            float(data["temperature"]),
            float(data["setpoint"]),
            bool(data["is_running"]),
        )
        self._show_status("", color="black")

    def refresh_status(self, *, clear_message: bool = True) -> None:
        try:
            data = self.client.status_all()
        except (RuntimeError, OSError, ConnectionError, TimeoutError) as exc:
            messagebox.showerror("Connection error", str(exc))
            LOGGER.error("Failed to refresh status: %s", exc)
            self._show_status(f"Error: {exc}")
            return

        self._apply_status_data(
            data["status"],
            float(data["temperature"]),
            float(data["setpoint"]),
            bool(data["is_running"]),
        )
        if clear_message:
            self._show_status("", color="black")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "host",
        nargs="?",
        default=None,
        help="Hostname or IP of the remote server (defaults to localhost)",
    )
    parser.add_argument(
        "--port", type=int, default=None, help="TCP port of the remote server"
    )
    parser.add_argument(
        "--timeout", type=float, default=None, help="Socket timeout in seconds"
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=None,
        help="Auto-refresh interval in milliseconds (default: 5000)",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Authentication token for the remote server",
    )
    parser.add_argument(
        "--auth-token-file",
        default=None,
        help="Path to a file containing the auth token (one line, stripped)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to configuration file (default: ~/.julabo_control.ini)",
    )
    parser.add_argument(
        "--tls",
        action="store_true",
        default=False,
        help="Use TLS encryption for the connection",
    )
    parser.add_argument(
        "--tls-ca",
        default=None,
        help="Path to CA certificate file for TLS verification",
    )
    parser.add_argument(
        "--temperature-log",
        default=None,
        help="Path to a CSV file for automatic temperature logging",
    )
    parser.add_argument(
        "--desktop-notifications",
        action="store_true",
        default=False,
        help="Enable desktop notifications for temperature alarms",
    )
    parser.add_argument(
        "--log-traffic",
        default=None,
        help="Path to a file for logging TCP request/response pairs",
    )
    parser.add_argument(
        "--font-size",
        type=int,
        default=None,
        help="Override the default font size (auto-detected from DPI if omitted)",
    )
    return parser.parse_args()


def main() -> None:  # pragma: no cover - CLI helper
    from .config import load_config

    args = parse_args()
    config_path = Path(args.config) if args.config else None
    config = load_config(config_path)
    remote_cfg = config.get("remote", {})

    host = args.host or remote_cfg.get("host", "localhost")
    port = args.port if args.port is not None else int(remote_cfg.get("port", "8765"))
    timeout = (
        args.timeout if args.timeout is not None else float(remote_cfg.get("timeout", "5.0"))
    )
    poll_interval = (
        args.poll_interval
        if args.poll_interval is not None
        else int(remote_cfg.get("poll_interval", "5000"))
    )
    auth_token = resolve_auth_token(
        args.auth_token,
        getattr(args, "auth_token_file", None),
        remote_cfg.get("auth_token"),
    )

    use_tls = args.tls or remote_cfg.get("tls", "").lower() in ("1", "true", "yes")
    tls_ca = args.tls_ca or remote_cfg.get("tls_ca")
    ssl_ctx: ssl.SSLContext | None = None
    if use_tls:
        ssl_ctx = ssl.create_default_context(cafile=tls_ca)
        if not tls_ca:
            # If no CA specified, don't verify (self-signed certs)
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

    client = RemoteChillerClient(
        host, port, timeout=timeout, auth_token=auth_token, ssl_context=ssl_ctx
    )

    temp_log = args.temperature_log or remote_cfg.get("temperature_log")
    desktop_notif = args.desktop_notifications or (
        remote_cfg.get("desktop_notifications", "").lower() in ("1", "true", "yes")
    )

    root = tk.Tk()
    RemoteChillerApp(
        root,
        client,
        poll_interval=poll_interval,
        log_file=temp_log,
        desktop_notifications=desktop_notif,
    )
    root.mainloop()


if __name__ == "__main__":  # pragma: no cover
    main()
