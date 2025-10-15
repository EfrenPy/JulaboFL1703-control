"""GUI client for remotely controlling a Julabo chiller."""

from __future__ import annotations

import argparse
import json
import socket
import tkinter as tk
from tkinter import messagebox
from typing import Any, Dict, Optional

from julabo_control.ui import TemperatureHistoryPlot, configure_default_fonts


class RemoteChillerClient:
    """Minimal TCP client for communicating with the remote control server."""

    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") + b"\n"
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.sendall(data)
            file = sock.makefile("rb")
            line = file.readline()
        if not line:
            raise ConnectionError("No response from server")
        response = json.loads(line.decode("utf-8"))
        if response.get("status") != "ok":
            raise RuntimeError(response.get("error", "Unknown error"))
        return response

    def command(self, name: str, value: Optional[Any] = None) -> Any:
        payload: Dict[str, Any] = {"command": name}
        if value is not None:
            payload["value"] = value
        response = self._send(payload)
        return response.get("result")


class RemoteChillerApp:
    """Tk application that visualises and proxies remote chiller commands."""

    def __init__(self, root: tk.Tk, client: RemoteChillerClient):
        self.root = root
        self.client = client

        configure_default_fonts()

        root.title("Julabo Remote Control")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="--")
        self.temperature_var = tk.StringVar(value="-- °C")
        self.setpoint_var = tk.StringVar(value="-- °C")
        self.running_var = tk.BooleanVar(value=False)
        self.running_text_var = tk.StringVar(value="Stopped")
        self.message_var = tk.StringVar(value="")
        self.new_setpoint_var = tk.StringVar()

        self.history_plot: Optional[TemperatureHistoryPlot] = None

        self._build_layout()
        self._set_message("", color="black")
        self.refresh_status()

    def _set_message(self, text: str, *, color: str = "red") -> None:
        self.message_var.set(text)
        self.message_label.configure(fg=color)

    def _build_layout(self) -> None:
        frame = tk.Frame(self.root, padx=20, pady=20)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(5, weight=1)

        tk.Label(frame, text="Status:").grid(row=0, column=0, sticky="w")
        tk.Label(frame, textvariable=self.status_var, width=20).grid(row=0, column=1, sticky="w")

        tk.Label(frame, text="Temperature (°C):").grid(row=1, column=0, sticky="w")
        tk.Label(frame, textvariable=self.temperature_var, width=20).grid(row=1, column=1, sticky="w")

        tk.Label(frame, text="Setpoint (°C):").grid(row=2, column=0, sticky="w")
        tk.Label(frame, textvariable=self.setpoint_var, width=20).grid(row=2, column=1, sticky="w")

        tk.Label(frame, text="Machine state:").grid(row=3, column=0, sticky="w")
        tk.Label(frame, textvariable=self.running_text_var, width=20).grid(row=3, column=1, sticky="w")

        control_frame = tk.LabelFrame(frame, text="Controls", padx=10, pady=10)
        control_frame.grid(row=4, column=0, columnspan=2, pady=(10, 0), sticky="ew")

        tk.Button(control_frame, text="Refresh", command=self.refresh_status).grid(row=0, column=0, padx=5)
        self.toggle_button = tk.Button(
            control_frame, text="Start machine", command=self._toggle_running
        )
        self.toggle_button.grid(row=0, column=1, padx=5)

        tk.Label(control_frame, text="Set new setpoint:").grid(row=1, column=0, sticky="e", pady=(10, 0))
        setpoint_entry = tk.Entry(control_frame, textvariable=self.new_setpoint_var, width=10)
        setpoint_entry.grid(row=1, column=1, pady=(10, 0))
        tk.Button(control_frame, text="Apply", command=self._apply_setpoint).grid(
            row=1, column=2, padx=5, pady=(10, 0)
        )

        self.message_label = tk.Label(frame, textvariable=self.message_var, fg="black")
        self.message_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 0))

        plot_frame = tk.LabelFrame(frame, text="Temperature Trend", padx=10, pady=10)
        plot_frame.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        frame.rowconfigure(6, weight=1)

        self.history_plot = TemperatureHistoryPlot(plot_frame)
        self.history_plot.widget.pack(fill="both", expand=True)
        self.history_plot.clear()

        for child in frame.winfo_children():
            child.grid_configure(padx=4, pady=4)

    def _update_running_state(self) -> None:
        running = self.running_var.get()
        self.running_text_var.set("Running" if running else "Stopped")
        self.toggle_button.configure(text="Stop machine" if running else "Start machine")

    def _toggle_running(self) -> None:
        target_state = not self.running_var.get()
        command = "start" if target_state else "stop"
        try:
            confirmed = bool(self.client.command(command))
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("Machine control error", str(exc))
            self._set_message(f"Error: {exc}")
            return

        self.refresh_status(clear_message=False)
        self._set_message(
            "Machine started" if confirmed else "Machine stopped",
            color="green" if confirmed else "red",
        )

    def _apply_setpoint(self) -> None:
        try:
            value = float(self.new_setpoint_var.get())
        except ValueError:
            messagebox.showwarning("Invalid input", "Please enter a numeric value.")
            return

        try:
            confirmed = float(self.client.command("set_setpoint", value))
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("Setpoint error", str(exc))
            self._set_message(f"Error: {exc}")
            return

        self.new_setpoint_var.set("")
        self.refresh_status(clear_message=False)
        self._set_message(
            f"Setpoint updated to {confirmed:.2f} °C",
            color="green",
        )

    def refresh_status(self, *, clear_message: bool = True) -> None:
        try:
            status = self.client.command("status")
            temperature = float(self.client.command("temperature"))
            setpoint = float(self.client.command("get_setpoint"))
            running = bool(self.client.command("is_running"))
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("Connection error", str(exc))
            self._set_message(f"Error: {exc}")
            return

        self.status_var.set(status)
        self.temperature_var.set(f"{temperature:.2f} °C")
        self.setpoint_var.set(f"{setpoint:.2f} °C")
        self.running_var.set(running)
        self._update_running_state()
        if self.history_plot is not None:
            self.history_plot.record(temperature)
        if clear_message:
            self._set_message("", color="black")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "host",
        nargs="?",
        default="pctpx4ctl.cern.ch",
        help="Hostname or IP of the remote server (defaults to pctpx4ctl.cern.ch)",
    )
    parser.add_argument("--port", type=int, default=8765, help="TCP port of the remote server")
    parser.add_argument("--timeout", type=float, default=5.0, help="Socket timeout in seconds")
    return parser.parse_args()


def main() -> None:  # pragma: no cover - CLI helper
    args = parse_args()
    client = RemoteChillerClient(args.host, args.port, timeout=args.timeout)

    root = tk.Tk()
    RemoteChillerApp(root, client)
    root.mainloop()


if __name__ == "__main__":  # pragma: no cover
    main()
