"""Shared GUI utilities for Julabo applications."""

from __future__ import annotations

import time
from typing import List, Tuple

import tkinter as tk
from tkinter import font as tkfont

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

_DEFAULT_FONT_NAMES = (
    "TkDefaultFont",
    "TkTextFont",
    "TkFixedFont",
    "TkMenuFont",
    "TkHeadingFont",
    "TkTooltipFont",
)


def configure_default_fonts(size: int = 12) -> None:
    """Increase the default Tk font size to improve readability."""

    for font_name in _DEFAULT_FONT_NAMES:
        try:
            tkfont.nametofont(font_name).configure(size=size)
        except tk.TclError:
            continue


class TemperatureHistoryPlot:
    """Simple helper that embeds a matplotlib plot inside a Tk widget."""

    def __init__(self, master: tk.Misc, *, max_points: int = 120):
        self._history: List[Tuple[float, float]] = []
        self._max_points = max_points

        self.figure = Figure(figsize=(6, 3), dpi=100)
        self.axes = self.figure.add_subplot(111)
        self.axes.set_xlabel("Time (min)")
        self.axes.set_ylabel("Temperature (°C)")
        self.axes.grid(True, linestyle="--", linewidth=0.5)
        (self._line,) = self.axes.plot([], [], marker="o", linestyle="-", color="#1f77b4")
        self.figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.figure, master=master)
        self.canvas.draw()

    @property
    def widget(self) -> tk.Widget:
        return self.canvas.get_tk_widget()

    def clear(self) -> None:
        self._history.clear()
        self._line.set_data([], [])
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

        temp_min = min(temps)
        temp_max = max(temps)
        if temp_min == temp_max:
            padding = max(0.5, abs(temp_min) * 0.05)
        else:
            padding = (temp_max - temp_min) * 0.1
        self.axes.set_ylim(temp_min - padding, temp_max + padding)

        self.canvas.draw_idle()
