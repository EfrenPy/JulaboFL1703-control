"""Command line interface for local Julabo control."""

from __future__ import annotations

import argparse
from typing import Iterable, Optional

import serial

from .core import (
    DEFAULT_TIMEOUT,
    JulaboChiller,
    JulaboError,
    SerialSettings,
    auto_detect_port,
    remember_port,
)
from .gui import run_gui


def _format_lines(lines: Iterable[str]) -> str:
    return "\n".join(lines)


def main(argv: Optional[Iterable[str]] = None) -> int:
    """Run the Julabo command line interface."""

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
            remember_port(settings.port)
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
            else:  # pragma: no cover - defensive programming
                raise AssertionError(f"Unknown command: {args.command}")

    try:
        print(_format_lines(run()))
        return 0
    except (JulaboError, TimeoutError, serial.SerialException) as exc:
        parser.error(str(exc))
        return 2
