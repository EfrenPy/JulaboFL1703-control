"""Graphical interfaces for local Julabo control."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import filedialog, messagebox

import serial

from .alarm import TemperatureAlarm
from .core import (
    DEFAULT_TIMEOUT,
    SETPOINT_MAX,
    SETPOINT_MIN,
    JulaboChiller,
    JulaboError,
    SerialSettings,
    remember_port,
)
from .schedule import ScheduleRunner, SetpointSchedule
from .temperature_logger import TemperatureFileLogger
from .ui import BaseChillerApp, TemperatureHistoryPlot, configure_default_fonts

LOGGER = logging.getLogger(__name__)


class ChillerApp(BaseChillerApp):
    """Tk application for local Julabo chiller control."""

    def __init__(
        self,
        root: tk.Tk,
        settings: SerialSettings | None,
        *,
        startup_error: BaseException | None = None,
        poll_interval: int = 5000,
        alarm_threshold: float = 2.0,
        log_file: str | None = None,
        alarm_log: str | None = None,
        desktop_notifications: bool = False,
        font_size: int | None = None,
    ):
        self.root = root
        self._chiller: JulaboChiller | None = None
        self._current_settings: SerialSettings | None = settings
        self._refresh_job: str | None = None
        self._flash_job: str | None = None
        self._closed = False
        self._timeout_value = settings.timeout if settings is not None else DEFAULT_TIMEOUT
        self._reconnect_delay = 1.0
        self._reconnect_delay_max = 30.0
        self._reconnect_delay_factor = 2.0
        self._schedule_runner: ScheduleRunner | None = None

        root.title("Julabo Chiller Control")
        configure_default_fonts(font_size)

        self.port_var = tk.StringVar(value=settings.port if settings is not None else "")
        self.entry_var = tk.StringVar()
        self.setpoint_var = tk.StringVar(value="--")
        self.temp_var = tk.StringVar(value="--")
        self.status_var = tk.StringVar()
        self.running_var = tk.BooleanVar(value=False)
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
            log_file=alarm_log,
        )

        self._build_layout()
        self._bind_shortcuts()
        self._center_window()

        connection_error = startup_error
        if settings is not None and connection_error is None:
            connection_error = self._attempt_connection()

        root.protocol("WM_DELETE_WINDOW", self.on_close)

        if connection_error is not None:  # pragma: no cover - GUI runtime feedback

            def _show_startup_error(err: BaseException | None = connection_error) -> None:
                messagebox.showerror("Connection error", str(err), parent=root)

            root.after(0, _show_startup_error)

        if self._chiller is not None:
            self.set_connected(self._chiller, settings)
            self._show_status("", color="black")
        else:
            self.set_connected(None, None)
            self._show_status("Connect the Julabo chiller and press Test connection.")

    # -- Connection management --

    def _attempt_connection(self) -> BaseException | None:
        """Try to connect to the chiller. Returns the error or None on success."""
        if self._current_settings is None:
            raise RuntimeError("Cannot attempt connection without serial settings")
        self._chiller = JulaboChiller(self._current_settings)
        try:
            self._chiller.connect()
        except (serial.SerialException, OSError, TimeoutError) as exc:  # pragma: no cover
            self._chiller = None
            return exc
        else:
            remember_port(self._current_settings.port)
            return None

    def set_connected(
        self,
        new_chiller: JulaboChiller | None,
        new_settings: SerialSettings | None,
    ) -> None:
        self._cancel_refresh()
        previous = self._chiller
        if previous is not None and previous is not new_chiller:
            previous.close()
        self._chiller = new_chiller
        self._current_settings = new_settings
        if new_chiller is not None and new_settings is not None:
            remember_port(new_settings.port)
            self.refresh_readings()
        else:
            self._clear_temperature_plot()

    def test_connection(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning(
                "Serial port", "Please enter a serial port path.", parent=self.root
            )
            return

        try:
            candidate = SerialSettings(port=port, timeout=self._timeout_value)
            with JulaboChiller(candidate) as new_chiller:
                new_chiller.identify()
        except (  # pragma: no cover
            serial.SerialException, OSError, TimeoutError, JulaboError,
        ) as exc:
            messagebox.showerror("Connection error", str(exc), parent=self.root)
            self._show_status(f"Connection error: {exc}")
            self.set_connected(None, None)
        else:
            LOGGER.info("Connection test passed for %s", port)
            self._show_status("Connected", color="green")
            new_conn = JulaboChiller(candidate)
            try:
                new_conn.connect()
            except (serial.SerialException, OSError, TimeoutError) as exc:  # pragma: no cover
                messagebox.showerror("Connection error", str(exc), parent=self.root)
                self._show_status(f"Connection error: {exc}")
                self.set_connected(None, None)
            else:
                self.set_connected(new_conn, candidate)

    # -- Polling & data --

    def refresh_readings(self) -> None:
        self._refresh_job = None
        if self._chiller is None:
            self._show_status("Not connected to Julabo chiller.")
            self.running_var.set(False)
            self._clear_temperature_plot()
            return

        try:
            setpoint = self._chiller.get_setpoint()
            temperature = self._chiller.get_temperature()
            running = self._chiller.is_running()
        except (JulaboError, TimeoutError, serial.SerialException, OSError) as exc:
            LOGGER.error("Error reading from chiller: %s", exc)
            self._show_status(f"Error: {exc}")
            if self._current_settings is not None:
                try:
                    self._chiller.close()
                except (serial.SerialException, OSError):
                    pass
                try:
                    self._chiller = JulaboChiller(self._current_settings)
                    self._chiller.connect()
                    LOGGER.info("Reconnected to %s", self._current_settings.port)
                    self._show_status("Reconnected", color="green")
                    self._reconnect_delay = 1.0
                except (serial.SerialException, OSError, TimeoutError, JulaboError) as reconn_exc:
                    LOGGER.error("Reconnect failed: %s", reconn_exc)
                    self._chiller = None
                    self._reconnect_delay = min(
                        self._reconnect_delay * self._reconnect_delay_factor,
                        self._reconnect_delay_max,
                    )
        else:
            self.setpoint_var.set(f"{setpoint:.2f} \u00b0C")
            self.temp_var.set(f"{temperature:.2f} \u00b0C")
            self.running_var.set(running)
            self._update_running_button()
            self._update_temperature_plot(temperature)
            if self.temperature_plot is not None:
                self.temperature_plot.set_setpoint(setpoint)
            self._log_temperature(temperature, setpoint)
            self.alarm.threshold = self.alarm_threshold_var.get()
            self.alarm.check(temperature, setpoint)
            self._tick_schedule()
            self._reconnect_delay = 1.0
        finally:
            if self.root.winfo_exists():
                next_delay = (
                    int(self._reconnect_delay * 1000)
                    if self._chiller is None
                    else self.poll_interval_var.get()
                )
                self._refresh_job = self.root.after(next_delay, self.refresh_readings)

    # -- User actions --

    def apply_setpoint(self) -> None:
        if self._chiller is None:
            self._show_status("Not connected to Julabo chiller.")
            return

        raw_value = self.entry_var.get().strip()
        try:
            value = float(raw_value)
        except ValueError:
            self._show_status("Invalid temperature value.")
            return

        if not (SETPOINT_MIN <= value <= SETPOINT_MAX):
            self._show_status(
                f"Setpoint must be between {SETPOINT_MIN} and {SETPOINT_MAX} \u00b0C."
            )
            return

        try:
            self._chiller.set_setpoint(value)
        except (  # pragma: no cover
            JulaboError, TimeoutError, serial.SerialException, OSError,
        ) as exc:
            messagebox.showerror("Setpoint error", str(exc), parent=self.root)
        else:
            self._show_status(f"Setpoint updated to {value:.2f} \u00b0C", color="green")
            self.entry_var.set("")
            self.refresh_readings()

    def toggle_running(self) -> None:
        if self._chiller is None:
            self._show_status("Not connected to Julabo chiller.")
            return

        target_state = not self.running_var.get()
        try:
            confirmed = self._chiller.set_running(target_state)
        except (  # pragma: no cover
            JulaboError, TimeoutError, serial.SerialException, OSError,
        ) as exc:
            messagebox.showerror("Machine control error", str(exc), parent=self.root)
        else:
            self.running_var.set(confirmed)
            self._update_running_button()
            self._show_status(
                "Machine started" if confirmed else "Machine stopped",
                color="green" if confirmed else "red",
            )

    # -- Schedule --

    def load_schedule(self) -> None:
        """Open a file dialog to load a setpoint schedule CSV."""
        path = filedialog.askopenfilename(
            title="Load setpoint schedule",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            parent=self.root,
        )
        if not path:
            return
        try:
            schedule = SetpointSchedule.load_csv(path)
        except (ValueError, OSError) as exc:  # pragma: no cover
            messagebox.showerror("Schedule error", str(exc), parent=self.root)
            return

        if self._chiller is None:
            self._show_status("Not connected — cannot start schedule.")
            return

        self._schedule_runner = ScheduleRunner(
            schedule, self._schedule_apply_setpoint
        )
        self._schedule_runner.start()
        if self.temperature_plot is not None:
            self.temperature_plot.set_schedule(schedule)
        self._show_status(
            f"Schedule loaded: {len(schedule.steps)} steps, "
            f"{schedule.duration_minutes:.1f} min",
            color="green",
        )

    def stop_schedule(self) -> None:
        if self._schedule_runner is not None:
            self._schedule_runner.stop()
            self._schedule_runner = None
            if self.temperature_plot is not None:
                self.temperature_plot.set_schedule(None)
            self._show_status("Schedule stopped", color="red")

    def _schedule_apply_setpoint(self, value: float) -> None:
        """Callback invoked by the schedule runner to change the setpoint."""
        if self._chiller is None:
            return
        try:
            self._chiller.set_setpoint(value)
        except (  # pragma: no cover
            JulaboError, TimeoutError, serial.SerialException, OSError,
        ) as exc:
            LOGGER.error("Schedule setpoint error: %s", exc)

    def _tick_schedule(self) -> None:
        if self._schedule_runner is None or not self._schedule_runner.is_running:
            return
        try:
            self._schedule_runner.tick()
        except (JulaboError, TimeoutError, serial.SerialException, OSError, ValueError) as exc:
            LOGGER.error("Schedule tick error: %s", exc)
            self._show_status(f"Schedule error: {exc}")
            self._schedule_runner.stop()
            self._schedule_runner = None
            if self.temperature_plot is not None:
                self.temperature_plot.set_schedule(None)
            return
        if self._schedule_runner.is_finished:
            self._show_status("Schedule completed", color="green")
            if self.temperature_plot is not None:
                self.temperature_plot.set_schedule(None)
            self._schedule_runner = None
        else:
            elapsed = self._schedule_runner.elapsed_minutes
            total = self._schedule_runner.schedule.duration_minutes
            pct = min(100, elapsed / total * 100) if total > 0 else 100
            self._show_status(
                f"Schedule: {elapsed:.1f}/{total:.1f} min ({pct:.0f}%)",
                color="blue",
            )

    # -- Layout & lifecycle --

    def _build_layout(self) -> None:
        self.main_frame = tk.Frame(self.root, padx=20, pady=20)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(1, weight=1)
        self.main_frame.grid_columnconfigure(2, weight=1)
        self.main_frame.grid_rowconfigure(10, weight=1)

        # Row 0: Serial port
        tk.Label(self.main_frame, text="Serial port:").grid(row=0, column=0, sticky=tk.W)
        port_entry = tk.Entry(self.main_frame, textvariable=self.port_var, width=20)
        port_entry.grid(row=0, column=1, sticky=tk.W)
        tk.Button(self.main_frame, text="Test connection", command=self.test_connection).grid(
            row=0, column=2, sticky=tk.W
        )

        # Row 1: Setpoint
        tk.Label(self.main_frame, text="Current setpoint:").grid(
            row=1, column=0, sticky=tk.W
        )
        tk.Label(self.main_frame, textvariable=self.setpoint_var).grid(
            row=1, column=1, sticky=tk.W
        )

        # Row 2: Temperature
        tk.Label(self.main_frame, text="Current temperature:").grid(
            row=2, column=0, sticky=tk.W
        )
        self.temp_label = tk.Label(self.main_frame, textvariable=self.temp_var)
        self.temp_label.grid(row=2, column=1, sticky=tk.W)

        # Row 3: New setpoint
        tk.Label(self.main_frame, text="New setpoint (\u00b0C):").grid(
            row=3, column=0, sticky=tk.W, pady=(8, 0)
        )
        entry = tk.Entry(self.main_frame, textvariable=self.entry_var, width=10)
        entry.grid(row=3, column=1, sticky=tk.W, pady=(8, 0))

        apply_button = tk.Button(self.main_frame, text="Apply", command=self.apply_setpoint)
        apply_button.grid(row=3, column=2, sticky=tk.W, padx=(8, 0), pady=(8, 0))

        # Row 4: Start/Stop + Export CSV
        self.toggle_button = tk.Button(
            self.main_frame, text="Start machine", command=self.toggle_running
        )
        self.toggle_button.grid(row=4, column=0, sticky=tk.W)
        tk.Button(self.main_frame, text="Export CSV", command=self.export_csv).grid(
            row=4, column=1, sticky=tk.W
        )

        # Row 5: Schedule controls
        tk.Button(self.main_frame, text="Load Schedule", command=self.load_schedule).grid(
            row=5, column=0, sticky=tk.W
        )
        tk.Button(self.main_frame, text="Stop Schedule", command=self.stop_schedule).grid(
            row=5, column=1, sticky=tk.W
        )

        # Row 6: Poll interval
        tk.Label(self.main_frame, text="Poll interval (ms):").grid(
            row=6, column=0, sticky=tk.W
        )
        tk.Spinbox(
            self.main_frame,
            from_=1000,
            to=60000,
            increment=1000,
            textvariable=self.poll_interval_var,
            width=8,
        ).grid(row=6, column=1, sticky=tk.W)

        # Row 7: Alarm threshold
        tk.Label(self.main_frame, text="Alarm threshold (\u00b0C):").grid(
            row=7, column=0, sticky=tk.W
        )
        tk.Spinbox(
            self.main_frame,
            from_=0.0,
            to=20.0,
            increment=0.5,
            textvariable=self.alarm_threshold_var,
            width=8,
            format="%.1f",
        ).grid(row=7, column=1, sticky=tk.W)

        # Row 8: Status
        self.status_label = tk.Label(self.main_frame, textvariable=self.status_var, fg="black")
        self.status_label.grid(row=8, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))

        # Row 9-10: Plot
        plot_frame = tk.LabelFrame(
            self.main_frame, text="Temperature trend", padx=10, pady=10
        )
        plot_frame.grid(row=10, column=0, columnspan=3, sticky=tk.NSEW, pady=(10, 0))

        self.temperature_plot = TemperatureHistoryPlot(plot_frame)
        self.temperature_plot.widget.pack(fill=tk.BOTH, expand=True)
        self._clear_temperature_plot()

        for child in self.main_frame.winfo_children():
            child.grid_configure(padx=4, pady=4)  # type: ignore[union-attr]

    def _bind_shortcuts(self) -> None:
        """Register keyboard shortcuts on the root window."""
        self.root.bind("<Control-r>", lambda _e: self.refresh_readings())
        self.root.bind("<Control-s>", lambda _e: self.export_csv())
        self.root.bind("<Escape>", lambda _e: self.on_close())

    def _center_window(self) -> None:
        """Place the window roughly in the middle of the active screen."""
        self.root.update_idletasks()
        try:
            self.root.eval(f"tk::PlaceWindow {self.root.winfo_toplevel()} center")
        except tk.TclError:
            width = self.root.winfo_width()
            height = self.root.winfo_height()
            x_offset = max((self.root.winfo_screenwidth() - width) // 2, 0)
            y_offset = max((self.root.winfo_screenheight() - height) // 2, 0)
            self.root.geometry(f"+{x_offset}+{y_offset}")

    def on_close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_refresh()
        self._stop_flash()
        if self._schedule_runner is not None:
            self._schedule_runner.stop()
            self._schedule_runner = None
        if self.temperature_logger is not None:
            self.temperature_logger.close()
        self.alarm.close()
        if self._chiller is not None:
            self._chiller.close()
        self.root.destroy()


def run_gui(
    settings: SerialSettings | None,
    *,
    startup_error: BaseException | None = None,
    poll_interval: int = 5000,
    alarm_threshold: float = 2.0,
    log_file: str | None = None,
    alarm_log: str | None = None,
    desktop_notifications: bool = False,
    font_size: int | None = None,
) -> None:
    """Launch a small Tk GUI for interactive temperature control."""

    root = tk.Tk()
    app = ChillerApp(
        root,
        settings,
        startup_error=startup_error,
        poll_interval=poll_interval,
        alarm_threshold=alarm_threshold,
        log_file=log_file,
        alarm_log=alarm_log,
        desktop_notifications=desktop_notifications,
        font_size=font_size,
    )
    try:
        root.mainloop()
    finally:
        app.on_close()
