"""GUI client for remotely controlling a Julabo chiller."""

import argparse
import json
import socket
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox
from typing import Any, Dict, List, Optional, Tuple

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


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
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        for font_name in (
            "TkDefaultFont",
            "TkTextFont",
            "TkFixedFont",
            "TkMenuFont",
            "TkHeadingFont",
            "TkTooltipFont",
        ):
            try:
                tkfont.nametofont(font_name).configure(size=12)
            except tk.TclError:
                pass

        self.status_var = tk.StringVar(value="--")
        self.temperature_var = tk.StringVar(value="-- °C")
        self.setpoint_var = tk.StringVar(value="-- °C")
        self.running_var = tk.BooleanVar(value=False)
        self.message_var = tk.StringVar(value="")
        self.message_label: Optional[tk.Label] = None
        self.temperature_history: List[Tuple[float, float]] = []
        self._history_retention_seconds = 12 * 60 * 60  # keep 12 hours of readings
        self._time_window_minutes = 5.0
        self.slider_var = tk.DoubleVar(master=self.root, value=0.0)
        self.timeline_slider: Optional[tk.Scale] = None

        self._build_layout()
        self._set_message("", color="black")
        self.refresh_status()

    def _set_message(self, text: str, *, color: str = "red") -> None:
        self.message_var.set(text)
        if self.message_label is not None:
            self.message_label.configure(fg=color)

    def _build_layout(self) -> None:
        frame = tk.Frame(self.root, padx=20, pady=20)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        tk.Label(frame, text="Status:").grid(row=0, column=0, sticky="w")
        tk.Label(frame, textvariable=self.status_var, width=20).grid(row=0, column=1, sticky="w")

        tk.Label(frame, text="Temperature (°C):").grid(row=1, column=0, sticky="w")
        tk.Label(frame, textvariable=self.temperature_var, width=20).grid(row=1, column=1, sticky="w")

        tk.Label(frame, text="Setpoint (°C):").grid(row=2, column=0, sticky="w")
        tk.Label(frame, textvariable=self.setpoint_var, width=20).grid(row=2, column=1, sticky="w")

        tk.Label(frame, text="Machine state:").grid(row=3, column=0, sticky="w")
        self.running_text_var = tk.StringVar(value="Stopped")
        tk.Label(frame, textvariable=self.running_text_var, width=20).grid(row=3, column=1, sticky="w")

        control_frame = tk.LabelFrame(frame, text="Controls", padx=10, pady=10)
        control_frame.grid(row=4, column=0, columnspan=2, pady=(10, 0), sticky="ew")

        tk.Button(control_frame, text="Refresh", command=self.refresh_status).grid(row=0, column=0, padx=5)
        self.toggle_button = tk.Button(control_frame, text="Start machine", command=self._toggle_running)
        self.toggle_button.grid(row=0, column=1, padx=5)

        tk.Label(control_frame, text="Set new setpoint:").grid(row=1, column=0, sticky="e", pady=(10, 0))
        self.new_setpoint_entry = tk.Entry(control_frame, width=10)
        self.new_setpoint_entry.grid(row=1, column=1, pady=(10, 0))
        tk.Button(control_frame, text="Apply", command=self._apply_setpoint).grid(row=1, column=2, padx=5, pady=(10, 0))

        self.message_label = tk.Label(frame, textvariable=self.message_var, fg="black")
        self.message_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 0))

        plot_frame = tk.LabelFrame(frame, text="Temperature Trend", padx=10, pady=10)
        plot_frame.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        frame.rowconfigure(6, weight=1)

        self.figure = Figure(figsize=(6, 3), dpi=100)
        self.axes = self.figure.add_subplot(111)
        self.axes.set_xlabel("Time (min)")
        self.axes.set_ylabel("Temperature (°C)")
        self.axes.grid(True, linestyle="--", linewidth=0.5)
        (self.temperature_line,) = self.axes.plot([], [], marker="o", linestyle="-", color="#1f77b4")
        self.figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        slider_container = tk.Frame(plot_frame)
        slider_container.pack(fill="x", expand=False, pady=(10, 0))
        tk.Label(slider_container, text="History offset (minutes):").pack(side="left")
        self.timeline_slider = tk.Scale(
            slider_container,
            variable=self.slider_var,
            from_=0.0,
            to=0.0,
            resolution=0.1,
            orient=tk.HORIZONTAL,
            command=self._on_slider_change,
        )
        self.timeline_slider.pack(side="left", fill="x", expand=True, padx=(8, 0))

        for child in frame.winfo_children():
            child.grid_configure(padx=4, pady=4)

        self._update_running_state()
        self._update_slider_range()

    def _update_running_state(self) -> None:
        self.running_text_var.set("Running" if self.running_var.get() else "Stopped")
        self.toggle_button.configure(
            text="Stop machine" if self.running_var.get() else "Start machine"
        )

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
            color="green",
        )

    def _apply_setpoint(self) -> None:
        try:
            value = float(self.new_setpoint_entry.get())
        except ValueError:
            messagebox.showwarning("Invalid input", "Please enter a numeric value.")
            return

        try:
            confirmed = float(self.client.command("set_setpoint", value))
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("Setpoint error", str(exc))
            self._set_message(f"Error: {exc}")
            return

        self.new_setpoint_entry.delete(0, tk.END)
        self.refresh_status(clear_message=False)
        self._set_message(
            f"Setpoint updated to {confirmed:.2f} °C",
            color="green",
        )

    def refresh_status(self, *, clear_message: bool = True) -> None:
        try:
            status = self.client.command("status")
            temperature = self.client.command("temperature")
            setpoint = self.client.command("get_setpoint")
            running = bool(self.client.command("is_running"))
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("Connection error", str(exc))
            self._set_message(f"Error: {exc}")
            return

        self.status_var.set(status)
        temperature_value = float(temperature)
        self.temperature_var.set(f"{temperature_value:.2f} °C")
        self.setpoint_var.set(f"{float(setpoint):.2f} °C")
        self.running_var.set(running)
        self._record_temperature(temperature_value)
        self._update_running_state()
        if clear_message:
            self._set_message("", color="black")

    def _record_temperature(self, value: float) -> None:
        timestamp = time.time()
        self.temperature_history.append((timestamp, value))
        self._trim_temperature_history()
        self._update_slider_range()
        self._update_temperature_plot()

    def _trim_temperature_history(self) -> None:
        if not self.temperature_history:
            return

        cutoff = self.temperature_history[-1][0] - self._history_retention_seconds
        if self.temperature_history[0][0] >= cutoff:
            return

        first_index = 0
        for idx, (timestamp, _value) in enumerate(self.temperature_history):
            if timestamp >= cutoff:
                first_index = idx
                break
        if first_index:
            self.temperature_history = self.temperature_history[first_index:]

    def _update_slider_range(self) -> None:
        if self.timeline_slider is None:
            return

        if len(self.temperature_history) < 2:
            self.timeline_slider.configure(to=0.0)
            if self.slider_var.get() != 0.0:
                self.slider_var.set(0.0)
            return

        start_time = self.temperature_history[0][0]
        end_time = self.temperature_history[-1][0]
        total_minutes = (end_time - start_time) / 60
        max_offset = max(0.0, total_minutes - self._time_window_minutes)

        current_to = float(self.timeline_slider.cget("to"))
        if abs(current_to - max_offset) > 1e-6:
            self.timeline_slider.configure(to=max_offset)
        if self.slider_var.get() > max_offset:
            self.slider_var.set(max_offset)

    def _on_slider_change(self, _value: str) -> None:
        self._update_temperature_plot()

    def _update_temperature_plot(self) -> None:
        if not self.temperature_history:
            self.temperature_line.set_data([], [])
            self.axes.set_xlim(0.0, 1.0)
            self.axes.set_ylim(0.0, 1.0)
            self.axes.figure.canvas.draw_idle()
            return

        self._update_slider_range()

        times, temps = zip(*self.temperature_history)
        offset_minutes = max(self.slider_var.get(), 0.0)
        end_time = times[-1] - offset_minutes * 60
        start_time = end_time - self._time_window_minutes * 60
        if start_time < times[0]:
            start_time = times[0]
            end_time = min(start_time + self._time_window_minutes * 60, times[-1])

        window_times: List[float] = []
        window_temps: List[float] = []
        for timestamp, temp in self.temperature_history:
            if start_time <= timestamp <= end_time:
                window_times.append(timestamp)
                window_temps.append(temp)

        if not window_times:
            window_times = [times[-1]]
            window_temps = [temps[-1]]
            start_time = window_times[0]

        elapsed_minutes = [(timestamp - start_time) / 60 for timestamp in window_times]
        self.temperature_line.set_data(elapsed_minutes, window_temps)

        x_span = elapsed_minutes[-1] if elapsed_minutes else 0.0
        self.axes.set_xlim(0.0, max(x_span, 1.0))

        temp_min = min(window_temps)
        temp_max = max(window_temps)
        if temp_min == temp_max:
            padding = max(0.5, abs(temp_min) * 0.05)
        else:
            padding = (temp_max - temp_min) * 0.1
        self.axes.set_ylim(temp_min - padding, temp_max + padding)

        self.axes.figure.canvas.draw_idle()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "host",
        nargs="?",
        default="localhost",
        help="Hostname or IP of the remote server (defaults to localhost)",
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
