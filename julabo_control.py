"""Julabo chiller remote control helper.

This module provides a small Python API and command line interface for
communicating with a Julabo recirculating chiller via the Julabo RS232
protocol.  The device needs to be in remote control (``rOFF``) mode and
connected to the host machine through a null-modem cable and an
RS232-to-USB converter.

Example usage from the command line::

    python -m julabo_control version
    python -m julabo_control get-setpoint
    python -m julabo_control set-setpoint 18.5
    python -m julabo_control start

The CLI wraps a thin :class:`JulaboChiller` class that manages the serial
connection and exposes typed helper methods for the most common
operations.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import serial
from serial.tools import list_ports


DEFAULT_BAUDRATE = 4800
DEFAULT_TIMEOUT = 2.0
PORT_CACHE_PATH = Path.home() / ".julabo_control_port"


@dataclass
class SerialSettings:
    """Serial port configuration required by the Julabo chiller."""

    port: str
    baudrate: int = DEFAULT_BAUDRATE
    timeout: float = DEFAULT_TIMEOUT
    bytesize: int = serial.SEVENBITS
    parity: str = serial.PARITY_EVEN
    stopbits: int = serial.STOPBITS_ONE
    rtscts: bool = True


class JulaboError(RuntimeError):
    """Raised when an unexpected error message is returned by the chiller."""


class JulaboChiller:
    """High level helper around a Julabo chiller.

    The chiller uses a very small command set over RS232. Every command
    is an ASCII string terminated by a carriage-return/line-feed pair.
    All responses follow the same structure.
    """

    def __init__(self, settings: SerialSettings):
        self._settings = settings
        self._serial: Optional[serial.Serial] = None

    def connect(self) -> None:
        """Open the serial connection if not already opened."""

        if self._serial is None:
            self._serial = serial.Serial(
                port=self._settings.port,
                baudrate=self._settings.baudrate,
                timeout=self._settings.timeout,
                bytesize=self._settings.bytesize,
                parity=self._settings.parity,
                stopbits=self._settings.stopbits,
                rtscts=self._settings.rtscts,
            )

    def close(self) -> None:
        """Close the serial connection."""

        if self._serial is not None:
            self._serial.close()
            self._serial = None

    # Allow use in ``with`` statements.
    def __enter__(self) -> "JulaboChiller":  # pragma: no cover - trivial
        self.connect()
        return self

    def __exit__(self, *_exc_info: object) -> None:  # pragma: no cover - trivial
        self.close()

    # --- Low level helpers -------------------------------------------------
    @property
    def serial(self) -> serial.Serial:
        if self._serial is None:
            raise RuntimeError("Serial connection has not been opened. Call connect() first.")
        return self._serial

    def _write(self, message: str) -> None:
        data = (message + "\r\n").encode("ascii")
        self.serial.write(data)

    def _readline(self) -> str:
        raw = self.serial.readline()
        if not raw:
            raise TimeoutError("No response from Julabo chiller (timeout).")
        return raw.decode("ascii", errors="replace").strip()

    def _query(self, command: str) -> str:
        self._write(command)
        response = self._readline()
        if response.lower().startswith("error"):
            raise JulaboError(response)
        return response

    # --- Public API --------------------------------------------------------
    def identify(self) -> str:
        """Return the controller identification string."""

        return self._query("version")

    def get_status(self) -> str:
        """Return the current status string (``status`` command)."""

        return self._query("status")

    def get_setpoint(self) -> float:
        """Return the active temperature setpoint in °C."""

        response = self._query("in_sp_00")
        return float(response)

    def set_setpoint(self, value: float) -> None:
        """Update the temperature setpoint."""

        # ``out_`` commands issued to the Julabo do not generate a reply.  The
        # controller silently applies the requested change, meaning waiting for a
        # response would time out.  Send the command and then explicitly read the
        # setpoint back to confirm it was applied.
        self._write(f"out_sp_00 {value:.1f}")

        # Read the setpoint back from the controller to confirm it has been
        # applied.  The device echoes the setpoint with one decimal place, so
        # compare within a small tolerance rather than relying on exact
        # floating point equality.
        confirmed_value = self.get_setpoint()
        if abs(confirmed_value - value) > 0.05:
            raise JulaboError(
                "Julabo chiller did not acknowledge the requested setpoint. "
                f"Expected {value:.2f} °C but read back {confirmed_value:.2f} °C."
            )

    def get_temperature(self) -> float:
        """Return the current process temperature in °C."""

        response = self._query("in_pv_00")
        return float(response)

    def set_running(self, start: bool) -> bool:
        """Start or stop the circulation pump and confirm the new state."""

        value = 1 if start else 0
        # ``out_`` commands do not send a response, so only issue the write and
        # rely on a follow-up ``in_`` command to confirm the change.
        self._write(f"out_mode_05 {value}")

        confirmed = self.is_running()
        if confirmed != start:
            raise JulaboError(
                "Julabo chiller did not acknowledge the requested cooling state. "
                "Expected {} but read back {}.".format(
                    "running" if start else "stopped",
                    "running" if confirmed else "stopped",
                )
            )

        return confirmed

    def is_running(self) -> bool:
        """Return ``True`` if the circulation pump is running."""

        response = self._query("in_mode_05")
        return response.strip() == "1"

    def start(self) -> bool:
        """Convenience wrapper to start circulation."""

        return self.set_running(True)

    def stop(self) -> bool:
        """Convenience wrapper to stop circulation."""

        return self.set_running(False)

    def raw_command(self, command: str) -> str:
        """Send a raw command and return the response.

        This can be used for commands that do not yet have a dedicated helper
        method implemented in this class.
        """

        return self._query(command)


# --- Command line interface -------------------------------------------------

def _format_lines(lines: Iterable[str]) -> str:
    return "\n".join(lines)


def _read_cached_port() -> Optional[str]:
    """Return the cached port path if it exists and is non-empty."""

    try:
        text = PORT_CACHE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _remember_port(port: str) -> None:
    """Persist the last working port for future runs."""

    try:
        PORT_CACHE_PATH.write_text(port, encoding="utf-8")
    except OSError:
        pass


def _probe_port(port: str, timeout: float) -> bool:
    """Return ``True`` if the provided port responds to an identify command."""

    settings = SerialSettings(port=port, timeout=timeout)
    try:
        with JulaboChiller(settings) as chiller:
            chiller.identify()
    except (JulaboError, TimeoutError, serial.SerialException):
        return False
    else:
        _remember_port(port)
        return True


def _candidate_ports() -> Iterable[str]:
    """Yield candidate serial device paths for Julabo detection."""

    seen = set()
    for port_info in list_ports.comports():
        if port_info.device and port_info.device not in seen:
            seen.add(port_info.device)
            yield port_info.device

    # ``serial.tools.list_ports`` already supports Windows but on some
    # configurations (notably when the USB adapter driver does not expose PnP
    # information) it may return an empty list. Provide manual fallbacks that
    # cover both Windows and POSIX device naming conventions so the automatic
    # probing keeps working across platforms.
    import sys

    if sys.platform.startswith("win"):
        # Windows COM ports start at 1. PySerial transparently handles the
        # ``COM10`` style names so probing them directly is sufficient.
        for index in range(1, 257):
            port = f"COM{index}"
            if port not in seen:
                seen.add(port)
                yield port
    else:
        import glob

        for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyS*"):
            for path in sorted(glob.glob(pattern)):
                if path not in seen:
                    seen.add(path)
                    yield path


def auto_detect_port(timeout: float) -> str:
    """Locate the Julabo serial adapter by probing available ports."""

    cached = _read_cached_port()
    if cached and _probe_port(cached, timeout):
        return cached

    for port in _candidate_ports():
        if port == cached:
            continue
        if _probe_port(port, timeout):
            return port

    raise serial.SerialException(
        "Unable to automatically locate the Julabo chiller. "
        "Connect it and try again or specify --port explicitly."
    )


def run_gui(settings: Optional[SerialSettings], *, startup_error: Optional[BaseException] = None) -> None:
    """Launch a small Tk GUI for interactive temperature control.

    ``settings`` may be ``None`` when no serial port could be determined. In
    that case the GUI is still shown but remains disconnected and displays an
    error dialog to inform the user.
    """

    import time
    import tkinter as tk
    from tkinter import messagebox
    from tkinter import font as tkfont

    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure

    chiller: Optional[JulaboChiller] = None
    refresh_job: Optional[str] = None
    timeout_value = settings.timeout if settings is not None else DEFAULT_TIMEOUT

    root = tk.Tk()
    root.title("Julabo Chiller Control")

    # Bump the default fonts used by Tk so the interface is easier to read on
    # high-DPI displays.
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

    connection_error = startup_error

    if settings is not None and connection_error is None:
        chiller = JulaboChiller(settings)
        try:
            chiller.connect()
        except Exception as exc:  # pragma: no cover - GUI runtime feedback
            connection_error = exc
            chiller = None
        else:
            _remember_port(settings.port)

    port_var = tk.StringVar(value=settings.port if settings is not None else "")
    entry_var = tk.StringVar()
    setpoint_var = tk.StringVar(value="--")
    temp_var = tk.StringVar(value="--")
    status_var = tk.StringVar()
    running_var = tk.BooleanVar(value=False)

    temperature_history: List[Tuple[float, float]] = []
    max_history_points = 120
    axes = None
    canvas = None
    temperature_line = None

    def clear_temperature_plot() -> None:
        temperature_history.clear()
        if axes is None or canvas is None or temperature_line is None:
            return
        temperature_line.set_data([], [])
        axes.set_xlim(0.0, 1.0)
        axes.set_ylim(0.0, 1.0)
        canvas.draw_idle()

    def update_temperature_plot() -> None:
        if not temperature_history or axes is None or canvas is None or temperature_line is None:
            return

        times, temps = zip(*temperature_history)
        start_time = times[0]
        elapsed_minutes = [(timestamp - start_time) / 60 for timestamp in times]
        temperature_line.set_data(elapsed_minutes, temps)

        if len(elapsed_minutes) == 1 or elapsed_minutes[-1] == 0:
            x_max = 1.0
        else:
            x_max = elapsed_minutes[-1]
        axes.set_xlim(0.0, max(x_max, 1.0))

        temp_min = min(temps)
        temp_max = max(temps)
        if temp_min == temp_max:
            padding = max(0.5, abs(temp_min) * 0.05)
        else:
            padding = (temp_max - temp_min) * 0.1
        axes.set_ylim(temp_min - padding, temp_max + padding)

        canvas.draw_idle()

    def record_temperature(value: float) -> None:
        temperature_history.append((time.time(), value))
        if len(temperature_history) > max_history_points:
            del temperature_history[:-max_history_points]
        update_temperature_plot()

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
            status_var.set("Not connected to Julabo chiller.")
            running_var.set(False)
            clear_temperature_plot()
            return

        try:
            setpoint = chiller.get_setpoint()
            temperature = chiller.get_temperature()
            running = chiller.is_running()
        except Exception as exc:  # pragma: no cover - GUI runtime feedback
            status_var.set(f"Error: {exc}")
        else:
            setpoint_var.set(f"{setpoint:.2f} °C")
            temp_var.set(f"{temperature:.2f} °C")
            running_var.set(running)
            update_running_button()
            status_var.set("")
            record_temperature(temperature)
        finally:
            if chiller is not None and root.winfo_exists():
                refresh_job = root.after(5000, refresh_readings)

    def update_running_button() -> None:
        if chiller is None:
            toggle_button.configure(state="disabled", text="Start cooling")
            running_var.set(False)
            return

        toggle_button.configure(state="normal")
        toggle_button.configure(
            text="Stop cooling" if running_var.get() else "Start cooling"
        )

    def set_connected(
        new_chiller: Optional[JulaboChiller], new_settings: Optional[SerialSettings]
    ) -> None:
        nonlocal chiller, timeout_value

        if chiller is not None and chiller is not new_chiller:
            try:
                chiller.close()
            except Exception:
                pass

        cancel_refresh()
        chiller = new_chiller
        clear_temperature_plot()

        if new_settings is not None:
            timeout_value = new_settings.timeout
            port_var.set(new_settings.port)
            _remember_port(new_settings.port)

        if chiller is not None:
            entry.configure(state="normal")
            apply_button.configure(state="normal")
            update_running_button()
            entry.focus_set()
            refresh_readings()
        else:
            entry.configure(state="disabled")
            apply_button.configure(state="disabled")
            port_entry.focus_set()
            update_running_button()
            status_var.set("Not connected to Julabo chiller.")

    def test_connection() -> None:
        port = port_var.get().strip()
        if not port:
            status_var.set("Enter a serial port path.")
            set_connected(None, None)
            return

        new_settings = SerialSettings(port=port, timeout=timeout_value)
        tester = JulaboChiller(new_settings)
        try:
            tester.connect()
            tester.identify()
        except Exception as exc:  # pragma: no cover - GUI runtime feedback
            tester.close()
            messagebox.showerror("Connection error", str(exc), parent=root)
            set_connected(None, None)
        else:
            status_var.set(f"Connected to {port}.")
            set_connected(tester, new_settings)

    def apply_setpoint() -> None:
        raw_value = entry_var.get().strip()
        if not raw_value:
            status_var.set("Enter a temperature first.")
            return

        if chiller is None:
            status_var.set("Not connected to Julabo chiller.")
            return

        try:
            value = float(raw_value)
        except ValueError:
            status_var.set("Invalid temperature value.")
            return

        try:
            chiller.set_setpoint(value)
        except Exception as exc:  # pragma: no cover - GUI runtime feedback
            messagebox.showerror("Setpoint error", str(exc), parent=root)
        else:
            status_var.set(f"Setpoint updated to {value:.2f} °C")
            entry_var.set("")
            refresh_readings()

    def toggle_running() -> None:
        if chiller is None:
            status_var.set("Not connected to Julabo chiller.")
            return

        target_state = not running_var.get()
        try:
            confirmed = chiller.set_running(target_state)
        except Exception as exc:  # pragma: no cover - GUI runtime feedback
            messagebox.showerror("Cooling control error", str(exc), parent=root)
        else:
            running_var.set(confirmed)
            update_running_button()
            status_var.set("Cooling started" if confirmed else "Cooling stopped")

    def on_close() -> None:
        if chiller is not None:
            chiller.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

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

    toggle_button = tk.Button(main_frame, text="Start cooling", command=toggle_running)
    toggle_button.grid(row=4, column=0, columnspan=3, sticky=tk.W)

    status_label = tk.Label(main_frame, textvariable=status_var, fg="red")
    status_label.grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))

    plot_frame = tk.LabelFrame(main_frame, text="Temperature trend", padx=10, pady=10)
    plot_frame.grid(row=6, column=0, columnspan=3, sticky=tk.NSEW, pady=(10, 0))

    figure = Figure(figsize=(6, 3), dpi=100)
    axes = figure.add_subplot(111)
    axes.set_xlabel("Time (min)")
    axes.set_ylabel("Temperature (°C)")
    axes.grid(True, linestyle="--", linewidth=0.5)
    (temperature_line,) = axes.plot([], [], marker="o", linestyle="-", color="#1f77b4")
    figure.tight_layout()

    canvas = FigureCanvasTkAgg(figure, master=plot_frame)
    canvas.draw()
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

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
        status_var.set("")
    else:
        set_connected(None, None)
        status_var.set("Connect the Julabo chiller and press Test connection.")

    try:
        root.mainloop()
    finally:
        cancel_refresh()
        if chiller is not None:
            chiller.close()


def main(argv: Optional[Iterable[str]] = None) -> int:
    """Run the Julabo command line interface."""

    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        help=(
            "Serial device path, e.g. /dev/ttyUSB0. If omitted the program "
            "will attempt to auto-detect the Julabo adapter."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Read timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("version", help="Show the device identification string")
    subparsers.add_parser("status", help="Show the current status string")
    subparsers.add_parser("get-setpoint", help="Read the current setpoint")

    set_sp_parser = subparsers.add_parser("set-setpoint", help="Update the setpoint")
    set_sp_parser.add_argument("value", type=float, help="Setpoint in °C")

    subparsers.add_parser("get-temperature", help="Read the current temperature")
    subparsers.add_parser("gui", help="Launch a simple Tk GUI controller")
    subparsers.add_parser("start", help="Start cooling/circulation")
    subparsers.add_parser("stop", help="Stop cooling/circulation")

    raw_parser = subparsers.add_parser("send", help="Send a raw command string")
    raw_parser.add_argument("command", help="Command to send, e.g. 'in_sp_00'")

    args = parser.parse_args(argv)

    if args.command == "gui":
        if args.port:
            run_gui(SerialSettings(port=args.port, timeout=args.timeout))
            return 0
        try:
            port = auto_detect_port(args.timeout)
        except serial.SerialException as exc:
            run_gui(None, startup_error=exc)
            return 2
        else:
            run_gui(SerialSettings(port=port, timeout=args.timeout))
            return 0

    port = args.port or auto_detect_port(args.timeout)
    settings = SerialSettings(port=port, timeout=args.timeout)

    def run() -> Iterable[str]:
        with JulaboChiller(settings) as chiller:
            _remember_port(settings.port)
            if args.command == "version":
                yield chiller.identify()
            elif args.command == "status":
                yield chiller.get_status()
            elif args.command == "get-setpoint":
                yield f"{chiller.get_setpoint():.2f}"
            elif args.command == "set-setpoint":
                chiller.set_setpoint(args.value)
                yield f"Setpoint updated to {args.value:.2f} °C"
            elif args.command == "get-temperature":
                yield f"{chiller.get_temperature():.2f}"
            elif args.command == "start":
                chiller.start()
                yield "Chiller started"
            elif args.command == "stop":
                chiller.stop()
                yield "Chiller stopped"
            elif args.command == "send":
                yield chiller.raw_command(args.command)
            elif args.command == "gui":
                raise AssertionError("GUI command handled separately")
            else:  # pragma: no cover - defensive programming
                raise AssertionError(f"Unknown command: {args.command}")

    try:
        print(_format_lines(run()))
        return 0
    except (JulaboError, TimeoutError, serial.SerialException) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":  # pragma: no cover - module CLI
    raise SystemExit(main())
