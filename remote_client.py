"""GUI client for remotely controlling a Julabo chiller."""
from __future__ import annotations

import argparse
import json
import socket
import tkinter as tk
from tkinter import messagebox
from typing import Any, Dict, Optional


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
    def __init__(self, root: tk.Tk, client: RemoteChillerClient):
        self.root = root
        self.client = client

        root.title("Julabo Remote Control")
        root.resizable(False, False)

        self.status_var = tk.StringVar(value="Unknown")
        self.temperature_var = tk.StringVar(value="Unknown")
        self.setpoint_var = tk.StringVar(value="Unknown")

        self._build_layout()
        self.refresh_status()

    def _build_layout(self) -> None:
        frame = tk.Frame(self.root, padx=10, pady=10)
        frame.grid(row=0, column=0, sticky="nsew")

        tk.Label(frame, text="Status:").grid(row=0, column=0, sticky="w")
        tk.Label(frame, textvariable=self.status_var, width=20).grid(row=0, column=1, sticky="w")

        tk.Label(frame, text="Temperature (°C):").grid(row=1, column=0, sticky="w")
        tk.Label(frame, textvariable=self.temperature_var, width=20).grid(row=1, column=1, sticky="w")

        tk.Label(frame, text="Setpoint (°C):").grid(row=2, column=0, sticky="w")
        tk.Label(frame, textvariable=self.setpoint_var, width=20).grid(row=2, column=1, sticky="w")

        control_frame = tk.LabelFrame(frame, text="Controls", padx=10, pady=10)
        control_frame.grid(row=3, column=0, columnspan=2, pady=(10, 0), sticky="ew")

        tk.Button(control_frame, text="Refresh", command=self.refresh_status).grid(row=0, column=0, padx=5)
        tk.Button(control_frame, text="Start", command=lambda: self._execute("start")).grid(row=0, column=1, padx=5)
        tk.Button(control_frame, text="Stop", command=lambda: self._execute("stop")).grid(row=0, column=2, padx=5)

        tk.Label(control_frame, text="Set new setpoint:").grid(row=1, column=0, sticky="e", pady=(10, 0))
        self.new_setpoint_entry = tk.Entry(control_frame, width=10)
        self.new_setpoint_entry.grid(row=1, column=1, pady=(10, 0))
        tk.Button(control_frame, text="Apply", command=self._apply_setpoint).grid(row=1, column=2, padx=5, pady=(10, 0))

    def _execute(self, command: str) -> None:
        try:
            self.client.command(command)
            self.refresh_status()
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("Command failed", str(exc))

    def _apply_setpoint(self) -> None:
        try:
            value = float(self.new_setpoint_entry.get())
        except ValueError:
            messagebox.showwarning("Invalid input", "Please enter a numeric value.")
            return

        try:
            self.client.command("set_setpoint", value)
            self.refresh_status()
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("Setpoint error", str(exc))

    def refresh_status(self) -> None:
        try:
            status = self.client.command("status")
            temperature = self.client.command("temperature")
            setpoint = self.client.command("get_setpoint")
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("Connection error", str(exc))
            return

        self.status_var.set(status)
        self.temperature_var.set(f"{float(temperature):.2f}")
        self.setpoint_var.set(f"{float(setpoint):.2f}")


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
