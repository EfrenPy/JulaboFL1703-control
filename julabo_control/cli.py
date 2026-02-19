"""Command line interface for local Julabo control."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterable, Sequence

import serial

from . import __version__
from .config import load_config
from .core import (
    DEFAULT_TIMEOUT,
    JulaboChiller,
    JulaboError,
    SerialSettings,
    auto_detect_port,
    forget_port,
    remember_port,
)
from .gui import run_gui

LOGGER = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _format_lines(lines: Iterable[str]) -> str:
    return "\n".join(lines)


def _configure_logging(verbose: bool, log_file: str | None = None) -> None:
    handlers: list = []
    if verbose:
        handlers.append(logging.StreamHandler())
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    if handlers:
        logging.basicConfig(
            level=logging.DEBUG,
            format=LOG_FORMAT,
            handlers=handlers,
        )


def _run_monitor(
    settings: SerialSettings,
    *,
    interval: float = 5.0,
    csv_path: str | None = None,
    count: int | None = None,
    overwrite: bool = True,
) -> int:
    """Live terminal monitor for headless/SSH sessions."""
    import sys
    import time

    from .temperature_logger import TemperatureFileLogger

    logger: TemperatureFileLogger | None = None
    if csv_path:
        logger = TemperatureFileLogger(csv_path)

    try:
        with JulaboChiller(settings) as chiller:
            remember_port(settings.port)
            readings = 0
            while True:
                try:
                    temperature = chiller.get_temperature()
                    setpoint = chiller.get_setpoint()
                    running = chiller.is_running()
                except (JulaboError, TimeoutError, serial.SerialException) as exc:
                    LOGGER.error("Read error: %s", exc)
                    time.sleep(interval)
                    continue

                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                line = (
                    f"{timestamp} | Temp: {temperature:.2f}°C | "
                    f"SP: {setpoint:.2f}°C | "
                    f"Running: {'Yes' if running else 'No'}"
                )

                if overwrite:
                    sys.stdout.write(f"\r{line}")
                    sys.stdout.flush()
                else:
                    print(line)

                if logger is not None:
                    logger.record(temperature, setpoint)

                readings += 1
                if count is not None and readings >= count:
                    break
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        if logger is not None:
            logger.close()
        if overwrite:
            print()  # newline after overwritten line

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Julabo command line interface."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--port",
        default=None,
        help=(
            "Serial device path, e.g. /dev/ttyUSB0. If omitted the program "
            "will attempt to auto-detect the Julabo adapter."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help=f"Read timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging to stderr",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Write log output to the specified file",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to configuration file (default: ~/.julabo_control.ini)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("version", help="Show the device identification string")
    subparsers.add_parser("status", help="Show the current status string")
    subparsers.add_parser("get-setpoint", help="Read the current setpoint")

    set_sp_parser = subparsers.add_parser("set-setpoint", help="Update the setpoint")
    set_sp_parser.add_argument("value", type=float, help="Setpoint in \u00b0C")

    subparsers.add_parser("get-temperature", help="Read the current temperature")

    gui_parser = subparsers.add_parser("gui", help="Launch a simple Tk GUI controller")
    gui_parser.add_argument(
        "--poll-interval",
        type=int,
        default=None,
        help="Refresh interval in milliseconds (default: 5000)",
    )
    gui_parser.add_argument(
        "--alarm-threshold",
        type=float,
        default=None,
        help="Temperature alarm threshold in \u00b0C (0 to disable, default: 2.0)",
    )
    gui_parser.add_argument(
        "--temperature-log",
        default=None,
        help="Path to a CSV file for automatic temperature logging",
    )
    gui_parser.add_argument(
        "--desktop-notifications",
        action="store_true",
        default=False,
        help="Enable desktop notifications for temperature alarms",
    )
    gui_parser.add_argument(
        "--alarm-log",
        default=None,
        help="Path to a CSV file for alarm event logging",
    )
    gui_parser.add_argument(
        "--font-size",
        type=int,
        default=None,
        help="Override the default font size (auto-detected from DPI if omitted)",
    )

    monitor_parser = subparsers.add_parser("monitor", help="Live terminal temperature monitor")
    monitor_parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds (default: 5)",
    )
    monitor_parser.add_argument(
        "--csv",
        default=None,
        help="Path to a CSV file for logging readings",
    )
    monitor_parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Exit after this many readings (default: unlimited)",
    )
    monitor_parser.add_argument(
        "--no-overwrite",
        action="store_true",
        default=False,
        help="Print one line per reading instead of overwriting",
    )

    subparsers.add_parser("forget-port", help="Remove the cached serial port")
    subparsers.add_parser("start", help="Start cooling/circulation")
    subparsers.add_parser("stop", help="Stop cooling/circulation")

    raw_parser = subparsers.add_parser("send", help="Send a raw command string")
    raw_parser.add_argument("raw_command", help="Command to send, e.g. 'in_sp_00'")

    args = parser.parse_args(argv)

    _configure_logging(args.verbose, getattr(args, "log_file", None))

    from pathlib import Path

    config_path = Path(args.config) if args.config else None
    config = load_config(config_path)
    serial_cfg = config.get("serial", {})
    gui_cfg = config.get("gui", {})

    port = args.port or serial_cfg.get("port")
    timeout = (
        args.timeout
        if args.timeout is not None
        else float(serial_cfg.get("timeout", str(DEFAULT_TIMEOUT)))
    )

    if args.command == "forget-port":
        removed = forget_port()
        if removed:
            print("Cached port removed.")
        else:
            print("No cached port to remove.")
        return 0

    if args.command == "gui":
        raw_poll = getattr(args, "poll_interval", None)
        poll_interval: int = (
            raw_poll if raw_poll is not None
            else int(gui_cfg.get("poll_interval", "5000"))
        )
        raw_threshold = getattr(args, "alarm_threshold", None)
        alarm_threshold: float = (
            raw_threshold if raw_threshold is not None
            else float(gui_cfg.get("alarm_threshold", "2.0"))
        )
        temp_log: str | None = (
            getattr(args, "temperature_log", None) or gui_cfg.get("temperature_log")
        )
        desktop_notif: bool = getattr(args, "desktop_notifications", False) or (
            gui_cfg.get("desktop_notifications", "").lower() in ("1", "true", "yes")
        )
        alarm_log: str | None = (
            getattr(args, "alarm_log", None) or gui_cfg.get("alarm_log")
        )
        font_size: int | None = getattr(args, "font_size", None)
        if font_size is None:
            raw_font = gui_cfg.get("font_size")
            if raw_font is not None:
                font_size = int(raw_font)
        if port:
            run_gui(
                SerialSettings(port=port, timeout=timeout),
                poll_interval=poll_interval,
                alarm_threshold=alarm_threshold,
                log_file=temp_log,
                alarm_log=alarm_log,
                desktop_notifications=desktop_notif,
                font_size=font_size,
            )
            return 0
        try:
            detected_port = auto_detect_port(timeout)
        except serial.SerialException as exc:
            run_gui(None, startup_error=exc, font_size=font_size)
            return 2
        else:
            run_gui(
                SerialSettings(port=detected_port, timeout=timeout),
                poll_interval=poll_interval,
                alarm_threshold=alarm_threshold,
                log_file=temp_log,
                alarm_log=alarm_log,
                desktop_notifications=desktop_notif,
                font_size=font_size,
            )
            return 0

    if args.command == "monitor":
        resolved_port = port or auto_detect_port(timeout)
        settings = SerialSettings(port=resolved_port, timeout=timeout)
        return _run_monitor(
            settings,
            interval=getattr(args, "interval", 5.0),
            csv_path=getattr(args, "csv", None),
            count=getattr(args, "count", None),
            overwrite=not getattr(args, "no_overwrite", False),
        )

    resolved_port = port or auto_detect_port(timeout)
    settings = SerialSettings(port=resolved_port, timeout=timeout)

    def run() -> Iterable[str]:
        with JulaboChiller(settings) as chiller:
            remember_port(settings.port)
            if args.command == "version":
                yield chiller.identify()
            elif args.command == "status":
                yield chiller.get_status()
            elif args.command == "get-setpoint":
                yield f"{chiller.get_setpoint():.2f}"
            elif args.command == "set-setpoint":
                chiller.set_setpoint(args.value)
                yield f"Setpoint updated to {args.value:.2f} \u00b0C"
            elif args.command == "get-temperature":
                yield f"{chiller.get_temperature():.2f}"
            elif args.command == "start":
                chiller.start()
                yield "Chiller started"
            elif args.command == "stop":
                chiller.stop()
                yield "Chiller stopped"
            elif args.command == "send":
                yield chiller.raw_command(args.raw_command)
            else:  # pragma: no cover - defensive programming
                raise AssertionError(f"Unknown command: {args.command}")

    try:
        print(_format_lines(run()))
        return 0
    except (JulaboError, TimeoutError, serial.SerialException) as exc:
        parser.error(str(exc))
        return 2
