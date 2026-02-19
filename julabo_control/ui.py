"""Shared GUI utilities for Julabo applications."""

from __future__ import annotations

import csv
import logging
import time
import tkinter as tk
from datetime import datetime, timezone
from tkinter import filedialog
from tkinter import font as tkfont

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from .alarm import TemperatureAlarm
from .schedule import SetpointSchedule
from .temperature_logger import TemperatureFileLogger

LOGGER = logging.getLogger(__name__)

ALARM_BG = "#ffcccc"
DEFAULT_BG = "#d9d9d9"

_DEFAULT_FONT_NAMES = (
    "TkDefaultFont",
    "TkTextFont",
    "TkFixedFont",
    "TkMenuFont",
    "TkHeadingFont",
    "TkTooltipFont",
)


def _detect_font_size(root: tk.Tk | None = None) -> int:
    """Detect an appropriate font size based on screen DPI."""
    base_dpi = 96.0
    try:
        if root is None:
            root = tk._default_root  # type: ignore[attr-defined]
        if root is not None:
            dpi = root.winfo_fpixels("1i")
            scaled = int(round(12 * dpi / base_dpi))
            return max(10, min(24, scaled))
    except (tk.TclError, AttributeError):
        pass
    return 12


def configure_default_fonts(size: int | None = None) -> None:
    """Set the default Tk font size. Auto-detects from DPI if *size* is None."""
    if size is None:
        size = _detect_font_size()
    for font_name in _DEFAULT_FONT_NAMES:
        try:
            tkfont.nametofont(font_name).configure(size=size)
        except tk.TclError:
            continue


class TemperatureHistoryPlot:
    """Simple helper that embeds a matplotlib plot inside a Tk widget."""

    def __init__(self, master: tk.Misc, *, max_points: int = 120):
        """Create a new temperature history plot.

        Parameters
        ----------
        master:
            Parent Tk widget that will contain the plot.
        max_points:
            Maximum number of data points retained in the history.
            120 points ~= 10 minutes at the default 5 s poll interval.
        """
        self._history: list[tuple[float, float]] = []
        self._max_points = max_points
        self._setpoint: float | None = None
        self._schedule: SetpointSchedule | None = None

        self.figure = Figure(figsize=(6, 3), dpi=100)
        self.axes = self.figure.add_subplot(111)
        self.axes.set_xlabel("Time (min)")
        self.axes.set_ylabel("Temperature (\u00b0C)")
        self.axes.grid(True, linestyle="--", linewidth=0.5)
        (self._line,) = self.axes.plot([], [], marker="o", linestyle="-", color="#1f77b4")
        self._setpoint_line = self.axes.axhline(
            y=0, color="#ff7f0e", linestyle="--", linewidth=1.0, visible=False, label="Setpoint"
        )
        (self._schedule_line,) = self.axes.plot(
            [], [], linestyle="--", color="green", alpha=0.5, linewidth=1.5, label="Schedule"
        )
        self._schedule_line.set_visible(False)
        self.figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.figure, master=master)
        self.canvas.draw()

    @property
    def widget(self) -> tk.Widget:
        return self.canvas.get_tk_widget()  # type: ignore[no-any-return]

    @property
    def history(self) -> list[tuple[float, float]]:
        """Return a copy of the recorded history."""
        return list(self._history)

    def set_setpoint(self, value: float | None) -> None:
        """Update the setpoint overlay line on the plot."""
        self._setpoint = value
        if value is not None:
            self._setpoint_line.set_ydata([value])
            self._setpoint_line.set_visible(True)
        else:
            self._setpoint_line.set_visible(False)
        self.canvas.draw_idle()

    def set_schedule(self, schedule: SetpointSchedule | None) -> None:
        """Set or clear a schedule ramp overlay on the plot."""
        self._schedule = schedule
        if schedule is not None and schedule.steps:
            times = [s.elapsed_minutes for s in schedule.steps]
            temps = [s.temperature for s in schedule.steps]
            self._schedule_line.set_data(times, temps)
            self._schedule_line.set_visible(True)
        else:
            self._schedule_line.set_data([], [])
            self._schedule_line.set_visible(False)
        self.canvas.draw_idle()

    def export_csv(self, file_path: str) -> int:
        """Write recorded history to a CSV file. Returns the number of rows written."""
        if not self._history:
            return 0
        start_time = self._history[0][0]
        with open(file_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp_utc", "elapsed_minutes", "temperature_c"])
            for ts, temp in self._history:
                utc_str = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                elapsed = (ts - start_time) / 60.0
                writer.writerow([utc_str, f"{elapsed:.2f}", f"{temp:.2f}"])
        return len(self._history)

    def clear(self) -> None:
        self._history.clear()
        self._line.set_data([], [])
        self._setpoint_line.set_visible(False)
        self._setpoint = None
        self._schedule = None
        self._schedule_line.set_data([], [])
        self._schedule_line.set_visible(False)
        self.axes.set_xlim(0.0, 1.0)
        self.axes.set_ylim(0.0, 1.0)
        self.canvas.draw_idle()

    def record(self, value: float, *, timestamp: float | None = None) -> None:
        if timestamp is None:
            timestamp = time.time()
        self._history.append((timestamp, value))
        if len(self._history) > self._max_points:
            self._history = self._history[-self._max_points :]
        self._update_plot()

    def _update_plot(self) -> None:
        if not self._history:
            return

        times, temps = zip(*self._history)
        start_time = times[0]
        elapsed_minutes = [(t - start_time) / 60 for t in times]
        self._line.set_data(elapsed_minutes, temps)

        if len(elapsed_minutes) == 1 or elapsed_minutes[-1] == 0:
            x_max = 1.0
        else:
            x_max = elapsed_minutes[-1]
        self.axes.set_xlim(0.0, max(x_max, 1.0))

        all_values = list(temps)
        if self._setpoint is not None:
            all_values.append(self._setpoint)
        if self._schedule is not None:
            all_values.extend(s.temperature for s in self._schedule.steps)

        temp_min = min(all_values)
        temp_max = max(all_values)
        if temp_min == temp_max:
            padding = max(0.5, abs(temp_min) * 0.05)
        else:
            padding = (temp_max - temp_min) * 0.1
        self.axes.set_ylim(temp_min - padding, temp_max + padding)

        self.canvas.draw_idle()


class BaseChillerApp:
    """Shared base class for local and remote chiller GUIs.

    Provides alarm callbacks, flash logic, CSV export, and status display.
    Subclasses must set ``self.main_frame``, ``self.temp_label``,
    ``self.toggle_button``, ``self.status_label``, and
    ``self.temperature_plot`` before calling any base methods.
    """

    root: tk.Tk
    _refresh_job: str | None
    _flash_job: str | None
    alarm: TemperatureAlarm
    temperature_plot: TemperatureHistoryPlot | None
    temperature_logger: TemperatureFileLogger | None
    main_frame: tk.Frame
    temp_label: tk.Label
    toggle_button: tk.Button
    status_label: tk.Label
    status_var: tk.StringVar
    running_var: tk.BooleanVar
    poll_interval_var: tk.IntVar
    alarm_threshold_var: tk.DoubleVar

    # -- Alarm callbacks & flash --

    def _on_alarm(self) -> None:
        self.temp_label.configure(fg="red")
        self.root.bell()
        self._start_flash()

    def _on_clear(self) -> None:
        self.temp_label.configure(fg="black")
        self._stop_flash()

    def _start_flash(self) -> None:
        def _flash() -> None:
            current = self.main_frame.cget("bg")
            new_bg = ALARM_BG if current != ALARM_BG else "SystemButtonFace"
            try:
                self.main_frame.configure(bg=new_bg)
            except tk.TclError:
                self.main_frame.configure(bg=ALARM_BG if current != ALARM_BG else DEFAULT_BG)
            self._flash_job = self.root.after(500, _flash)

        if self._flash_job is None:
            _flash()

    def _stop_flash(self) -> None:
        if self._flash_job is not None:
            self.root.after_cancel(self._flash_job)
            self._flash_job = None
        try:
            self.main_frame.configure(bg="SystemButtonFace")
        except tk.TclError:
            self.main_frame.configure(bg=DEFAULT_BG)

    # -- Display helpers --

    def _show_status(self, message: str, *, color: str = "red") -> None:
        self.status_var.set(message)
        self.status_label.configure(fg=color)

    def _update_running_button(self) -> None:
        if self.running_var.get():
            self.toggle_button.configure(text="Stop machine")
        else:
            self.toggle_button.configure(text="Start machine")

    def _log_temperature(self, temperature: float, setpoint: float) -> None:
        if self.temperature_logger is not None:
            try:
                self.temperature_logger.record(temperature, setpoint)
            except OSError as exc:
                LOGGER.debug("Temperature logging failed: %s", exc)

    def _update_temperature_plot(self, value: float) -> None:
        if self.temperature_plot is not None:
            self.temperature_plot.record(value)

    def _clear_temperature_plot(self) -> None:
        if self.temperature_plot is not None:
            self.temperature_plot.clear()

    def _cancel_refresh(self) -> None:
        if self._refresh_job is not None:
            try:
                self.root.after_cancel(self._refresh_job)
            except (tk.TclError, ValueError):
                pass
            self._refresh_job = None

    def export_csv(self) -> None:
        if self.temperature_plot is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            parent=self.root,
        )
        if not path:
            return
        count = self.temperature_plot.export_csv(path)
        self._show_status(f"Exported {count} rows to CSV", color="green")
