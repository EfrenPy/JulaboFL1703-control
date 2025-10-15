"""TCP server exposing Julabo chiller controls over the network."""
from __future__ import annotations

import argparse
import json
import logging
import signal
import socketserver
import threading
import time
from typing import Any, Dict, Tuple

import serial

from julabo_control import (
    DEFAULT_BAUDRATE,
    DEFAULT_TIMEOUT,
    JulaboChiller,
    JulaboError,
    SerialSettings,
    auto_detect_port,
)


LOGGER = logging.getLogger(__name__)

RETRY_DELAY = 5.0


class JulaboTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Threaded TCP server that proxies requests to a :class:`JulaboChiller`."""

    allow_reuse_address = True

    def __init__(self, server_address: Tuple[str, int], chiller: JulaboChiller):
        super().__init__(server_address, JulaboRequestHandler)
        self.chiller = chiller
        self._lock = threading.Lock()

    def process_command(self, message: Dict[str, Any]) -> Dict[str, Any]:
        command = message.get("command")
        if not command:
            raise ValueError("Missing 'command' in request payload")

        with self._lock:
            if command == "identify":
                result = self.chiller.identify()
            elif command == "status":
                result = self.chiller.get_status()
            elif command == "get_setpoint":
                result = self.chiller.get_setpoint()
            elif command == "set_setpoint":
                value = message.get("value")
                if value is None:
                    raise ValueError("'set_setpoint' requires a numeric 'value'")
                self.chiller.set_setpoint(float(value))
                result = "ok"
            elif command == "temperature":
                result = self.chiller.get_temperature()
            elif command == "start":
                self.chiller.start()
                result = "ok"
            elif command == "stop":
                self.chiller.stop()
                result = "ok"
            elif command == "ping":
                result = "pong"
            else:
                raise ValueError(f"Unsupported command: {command}")

        return {"status": "ok", "result": result}


class JulaboRequestHandler(socketserver.StreamRequestHandler):
    """Handle a single TCP connection."""

    def handle(self) -> None:  # pragma: no cover - network side effects
        while True:
            raw = self.rfile.readline()
            if not raw:
                break
            raw = raw.strip()
            if not raw:
                continue

            try:
                message = json.loads(raw.decode("utf-8"))
                response = self.server.process_command(message)
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.exception("Failed to process message: %s", raw)
                response = {"status": "error", "error": str(exc)}

            data = json.dumps(response).encode("utf-8") + b"\n"
            self.wfile.write(data)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "serial_port",
        nargs="?",
        help=(
            "Serial port path for the Julabo chiller. If omitted the server tries "
            "to auto-detect the adapter."
        ),
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host interface to bind the TCP server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=8765, help="TCP port number to listen on (default: 8765)"
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=DEFAULT_BAUDRATE,
        help="Serial baudrate (default matches Julabo requirements)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="Serial read timeout in seconds",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging output",
    )
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s")


def main() -> None:  # pragma: no cover - CLI helper
    args = parse_arguments()
    configure_logging(args.verbose)

    chiller: JulaboChiller
    while True:
        if args.serial_port:
            serial_port = args.serial_port
            LOGGER.info("Using configured Julabo serial port %s", serial_port)
        else:
            try:
                serial_port = auto_detect_port(args.timeout)
            except serial.SerialException as exc:
                LOGGER.warning(
                    "Unable to locate Julabo chiller automatically: %s. "
                    "Retrying in %.1f seconds...",
                    exc,
                    RETRY_DELAY,
                )
                time.sleep(RETRY_DELAY)
                continue
            LOGGER.info("Auto-detected Julabo serial port at %s", serial_port)

        settings = SerialSettings(
            port=serial_port,
            baudrate=args.baudrate,
            timeout=args.timeout,
        )

        chiller = JulaboChiller(settings)
        try:
            chiller.connect()
            chiller.identify()
        except (JulaboError, TimeoutError, serial.SerialException) as exc:
            chiller.close()
            LOGGER.error("Failed to connect to Julabo on %s: %s", serial_port, exc)
            if args.serial_port:
                raise SystemExit(2) from exc
            LOGGER.info("Will retry detection in %.1f seconds", RETRY_DELAY)
            time.sleep(RETRY_DELAY)
            continue

        break

    server = JulaboTCPServer((args.host, args.port), chiller)
    LOGGER.info("Listening on %s:%s", args.host, args.port)

    def handle_signal(signum: int, _frame: Any) -> None:  # pragma: no cover - signal handler
        LOGGER.info("Received signal %s, shutting down.", signum)
        server.shutdown()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        server.serve_forever()
    finally:
        LOGGER.info("Closing server")
        server.server_close()
        chiller.close()


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
