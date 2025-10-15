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
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

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

        self._query(f"out_sp_00 {value:.1f}")

    def get_temperature(self) -> float:
        """Return the current process temperature in °C."""

        response = self._query("in_pv_00")
        return float(response)

    def set_running(self, start: bool) -> None:
        """Start or stop the circulation pump."""

        value = 1 if start else 0
        self._query(f"out_mode_05 {value}")

    def start(self) -> None:
        """Convenience wrapper to start circulation."""

        self.set_running(True)

    def stop(self) -> None:
        """Convenience wrapper to stop circulation."""

        self.set_running(False)

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

    import tkinter as tk
    from tkinter import messagebox

    chiller: Optional[JulaboChiller] = None
    refresh_job: Optional[str] = None
    timeout_value = settings.timeout if settings is not None else DEFAULT_TIMEOUT

    root = tk.Tk()
    root.title("Julabo Chiller Control")

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
            return

        try:
            setpoint = chiller.get_setpoint()
            temperature = chiller.get_temperature()
        except Exception as exc:  # pragma: no cover - GUI runtime feedback
            status_var.set(f"Error: {exc}")
        else:
            setpoint_var.set(f"{setpoint:.2f} °C")
            temp_var.set(f"{temperature:.2f} °C")
            status_var.set("")
        finally:
            if chiller is not None and root.winfo_exists():
                refresh_job = root.after(5000, refresh_readings)

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

        if new_settings is not None:
            timeout_value = new_settings.timeout
            port_var.set(new_settings.port)
            _remember_port(new_settings.port)

        if chiller is not None:
            entry.configure(state="normal")
            apply_button.configure(state="normal")
            entry.focus_set()
            refresh_readings()
        else:
            entry.configure(state="disabled")
            apply_button.configure(state="disabled")
            port_entry.focus_set()
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

    def on_close() -> None:
        if chiller is not None:
            chiller.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    main_frame = tk.Frame(root, padx=12, pady=12)
    main_frame.pack(fill=tk.BOTH, expand=True)

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

    status_label = tk.Label(main_frame, textvariable=status_var, fg="red")
    status_label.grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))

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
