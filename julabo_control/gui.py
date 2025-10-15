"""Graphical interfaces for local Julabo control."""

from __future__ import annotations

from typing import Optional

import tkinter as tk
from tkinter import messagebox

from .core import DEFAULT_TIMEOUT, JulaboChiller, SerialSettings, remember_port
from .ui import TemperatureHistoryPlot, configure_default_fonts


def run_gui(
    settings: Optional[SerialSettings], *, startup_error: Optional[BaseException] = None
) -> None:
    """Launch a small Tk GUI for interactive temperature control."""

    chiller: Optional[JulaboChiller] = None
    refresh_job: Optional[str] = None
    timeout_value = settings.timeout if settings is not None else DEFAULT_TIMEOUT

    root = tk.Tk()
    root.title("Julabo Chiller Control")

    configure_default_fonts()

    connection_error = startup_error

    if settings is not None and connection_error is None:
        chiller = JulaboChiller(settings)
        try:
            chiller.connect()
        except Exception as exc:  # pragma: no cover - GUI runtime feedback
            connection_error = exc
            chiller = None
        else:
            remember_port(settings.port)

    port_var = tk.StringVar(value=settings.port if settings is not None else "")
    entry_var = tk.StringVar()
    setpoint_var = tk.StringVar(value="--")
    temp_var = tk.StringVar(value="--")
    status_var = tk.StringVar()
    running_var = tk.BooleanVar(value=False)

    temperature_plot: Optional[TemperatureHistoryPlot] = None

    def show_status(message: str, *, color: str = "red") -> None:
        status_var.set(message)
        status_label.configure(fg=color)

    def clear_temperature_plot() -> None:
        if temperature_plot is not None:
            temperature_plot.clear()

    def update_temperature_plot(value: float) -> None:
        if temperature_plot is not None:
            temperature_plot.record(value)

    def cancel_refresh() -> None:
        nonlocal refresh_job
        if refresh_job is not None:
            try:
                root.after_cancel(refresh_job)
            except Exception:
                pass
            refresh_job = None

    def refresh_readings() -> None:
        nonlocal refresh_job
        refresh_job = None
        if chiller is None:
            show_status("Not connected to Julabo chiller.")
            running_var.set(False)
            clear_temperature_plot()
            return

        try:
            setpoint = chiller.get_setpoint()
            temperature = chiller.get_temperature()
            running = chiller.is_running()
        except Exception as exc:  # pragma: no cover - GUI runtime feedback
            show_status(f"Error: {exc}")
        else:
            setpoint_var.set(f"{setpoint:.2f} °C")
            temp_var.set(f"{temperature:.2f} °C")
            running_var.set(running)
            update_running_button()
            update_temperature_plot(temperature)
        finally:
            if chiller is not None and root.winfo_exists():
                refresh_job = root.after(5000, refresh_readings)

    def update_running_button() -> None:
        if running_var.get():
            toggle_button.configure(text="Stop machine")
        else:
            toggle_button.configure(text="Start machine")

    def set_connected(
        new_chiller: Optional[JulaboChiller], new_settings: Optional[SerialSettings]
    ) -> None:
        nonlocal chiller
        cancel_refresh()
        previous = chiller
        if previous is not None and previous is not new_chiller:
            previous.close()
        chiller = new_chiller
        if new_chiller is not None and new_settings is not None:
            remember_port(new_settings.port)
            refresh_readings()
        else:
            clear_temperature_plot()

    def test_connection() -> None:
        nonlocal chiller
        port = port_var.get().strip()
        if not port:
            messagebox.showwarning("Serial port", "Please enter a serial port path.", parent=root)
            return

        try:
            candidate = SerialSettings(port=port, timeout=timeout_value)
            with JulaboChiller(candidate) as new_chiller:
                new_chiller.identify()
        except Exception as exc:  # pragma: no cover - GUI runtime feedback
            messagebox.showerror("Connection error", str(exc), parent=root)
            show_status(f"Connection error: {exc}")
            set_connected(None, None)
        else:
            show_status("Connected", color="green")
            chiller = JulaboChiller(candidate)
            try:
                chiller.connect()
            except Exception as exc:  # pragma: no cover - GUI runtime feedback
                messagebox.showerror("Connection error", str(exc), parent=root)
                show_status(f"Connection error: {exc}")
                chiller = None
            else:
                set_connected(chiller, candidate)

    def apply_setpoint() -> None:
        if chiller is None:
            show_status("Not connected to Julabo chiller.")
            return

        raw_value = entry_var.get().strip()
        try:
            value = float(raw_value)
        except ValueError:
            show_status("Invalid temperature value.")
            return

        try:
            chiller.set_setpoint(value)
        except Exception as exc:  # pragma: no cover - GUI runtime feedback
            messagebox.showerror("Setpoint error", str(exc), parent=root)
        else:
            show_status(f"Setpoint updated to {value:.2f} °C", color="green")
            entry_var.set("")
            refresh_readings()

    def toggle_running() -> None:
        if chiller is None:
            show_status("Not connected to Julabo chiller.")
            return

        target_state = not running_var.get()
        try:
            confirmed = chiller.set_running(target_state)
        except Exception as exc:  # pragma: no cover - GUI runtime feedback
            messagebox.showerror("Machine control error", str(exc), parent=root)
        else:
            running_var.set(confirmed)
            update_running_button()
            show_status(
                "Machine started" if confirmed else "Machine stopped",
                color="green" if confirmed else "red",
            )

    def on_close() -> None:
        cancel_refresh()
        if chiller is not None:
            chiller.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", lambda: on_close())

    main_frame = tk.Frame(root, padx=20, pady=20)
    main_frame.pack(fill=tk.BOTH, expand=True)

    main_frame.grid_columnconfigure(0, weight=1)
    main_frame.grid_columnconfigure(1, weight=1)
    main_frame.grid_columnconfigure(2, weight=1)
    main_frame.grid_rowconfigure(6, weight=1)

    tk.Label(main_frame, text="Serial port:").grid(row=0, column=0, sticky=tk.W)
    port_entry = tk.Entry(main_frame, textvariable=port_var, width=20)
    port_entry.grid(row=0, column=1, sticky=tk.W)
    tk.Button(main_frame, text="Test connection", command=test_connection).grid(
        row=0, column=2, sticky=tk.W
    )

    tk.Label(main_frame, text="Current setpoint:").grid(row=1, column=0, sticky=tk.W)
    tk.Label(main_frame, textvariable=setpoint_var).grid(row=1, column=1, sticky=tk.W)

    tk.Label(main_frame, text="Current temperature:").grid(row=2, column=0, sticky=tk.W)
    tk.Label(main_frame, textvariable=temp_var).grid(row=2, column=1, sticky=tk.W)

    tk.Label(main_frame, text="New setpoint (°C):").grid(row=3, column=0, sticky=tk.W, pady=(8, 0))
    entry = tk.Entry(main_frame, textvariable=entry_var, width=10)
    entry.grid(row=3, column=1, sticky=tk.W, pady=(8, 0))

    apply_button = tk.Button(main_frame, text="Apply", command=apply_setpoint)
    apply_button.grid(row=3, column=2, sticky=tk.W, padx=(8, 0), pady=(8, 0))

    toggle_button = tk.Button(main_frame, text="Start machine", command=toggle_running)
    toggle_button.grid(row=4, column=0, columnspan=3, sticky=tk.W)

    status_label = tk.Label(main_frame, textvariable=status_var, fg="black")
    status_label.grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))

    plot_frame = tk.LabelFrame(main_frame, text="Temperature trend", padx=10, pady=10)
    plot_frame.grid(row=6, column=0, columnspan=3, sticky=tk.NSEW, pady=(10, 0))

    temperature_plot = TemperatureHistoryPlot(plot_frame)
    temperature_plot.widget.pack(fill=tk.BOTH, expand=True)
    clear_temperature_plot()

    for child in main_frame.winfo_children():
        child.grid_configure(padx=4, pady=4)

    def center_window(window: tk.Tk) -> None:
        """Place ``window`` roughly in the middle of the active screen."""

        window.update_idletasks()
        try:
            window.eval(f"tk::PlaceWindow {window.winfo_toplevel()} center")
        except tk.TclError:
            width = window.winfo_width()
            height = window.winfo_height()
            x_offset = max((window.winfo_screenwidth() - width) // 2, 0)
            y_offset = max((window.winfo_screenheight() - height) // 2, 0)
            window.geometry(f"+{x_offset}+{y_offset}")

    center_window(root)

    if connection_error is not None:  # pragma: no cover - GUI runtime feedback
        root.after(
            0,
            lambda err=connection_error: messagebox.showerror(
                "Connection error", str(err), parent=root
            ),
        )

    if chiller is not None:
        set_connected(chiller, settings)
        show_status("", color="black")
    else:
        set_connected(None, None)
        show_status("Connect the Julabo chiller and press Test connection.")

    try:
        root.mainloop()
    finally:
        cancel_refresh()
        if chiller is not None:
            chiller.close()
