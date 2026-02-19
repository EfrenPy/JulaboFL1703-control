"""Core serial communication helpers for Julabo chillers."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import serial
from serial.tools import list_ports

LOGGER = logging.getLogger(__name__)

DEFAULT_BAUDRATE = 4800
DEFAULT_TIMEOUT = 2.0
PORT_CACHE_PATH = Path.home() / ".julabo_control_port"

SETPOINT_MIN = -50.0
SETPOINT_MAX = 200.0


@runtime_checkable
class ChillerBackend(Protocol):
    """Structural interface for any chiller backend (real or simulated)."""

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def identify(self) -> str: ...
    def get_status(self) -> str: ...
    def get_setpoint(self) -> float: ...
    def set_setpoint(self, value: float) -> None: ...
    def get_temperature(self) -> float: ...
    def is_running(self) -> bool: ...
    def set_running(self, start: bool) -> bool: ...
    def start(self) -> bool: ...
    def stop(self) -> bool: ...


@dataclass
class SerialSettings:
    """Serial port configuration required by the Julabo chiller."""

    port: str
    baudrate: int = DEFAULT_BAUDRATE
    timeout: float = DEFAULT_TIMEOUT
    bytesize: int = serial.SEVENBITS
    parity: str = serial.PARITY_EVEN
    stopbits: float = serial.STOPBITS_ONE
    rtscts: bool = True


class JulaboError(RuntimeError):
    """Raised when an unexpected error message is returned by the chiller."""


class JulaboChiller:
    """High level helper around a Julabo chiller."""

    _MIN_COMMAND_INTERVAL = 0.1  # 100ms minimum between serial commands

    def __init__(self, settings: SerialSettings):
        self._settings = settings
        self._serial: serial.Serial | None = None
        self._last_command_time: float = 0.0

    def connect(self) -> None:
        """Open the serial connection if not already opened."""

        if self._serial is None:
            LOGGER.info("Opening serial connection to %s", self._settings.port)
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
            LOGGER.info("Closing serial connection to %s", self._settings.port)
            self._serial.close()
            self._serial = None

    def __enter__(self) -> JulaboChiller:  # pragma: no cover - trivial
        self.connect()
        return self

    def __exit__(self, *_exc_info: object) -> None:  # pragma: no cover - trivial
        self.close()

    @property
    def settings(self) -> SerialSettings:
        """Return the serial settings used by this chiller."""
        return self._settings

    @property
    def serial(self) -> serial.Serial:
        if self._serial is None:
            raise RuntimeError("Serial connection has not been opened. Call connect() first.")
        return self._serial

    def _enforce_rate_limit(self) -> None:
        """Ensure a minimum interval between serial commands."""
        now = time.monotonic()
        elapsed = now - self._last_command_time
        if elapsed < self._MIN_COMMAND_INTERVAL:
            time.sleep(self._MIN_COMMAND_INTERVAL - elapsed)
        self._last_command_time = time.monotonic()

    def _write(self, message: str) -> None:
        self._enforce_rate_limit()
        data = (message + "\r\n").encode("ascii")
        LOGGER.debug("TX: %s", message)
        self.serial.write(data)

    def _readline(self) -> str:
        raw = self.serial.readline()
        if not raw:
            raise TimeoutError("No response from Julabo chiller (timeout).")
        decoded = str(raw.decode("ascii", errors="replace").strip())
        LOGGER.debug("RX: %s", decoded)
        return decoded

    def _query(self, command: str) -> str:
        self._write(command)
        response = self._readline()
        if response.lower().startswith("error"):
            LOGGER.warning("Error response for '%s': %s", command, response)
            raise JulaboError(response)
        return response

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

        if not (SETPOINT_MIN <= value <= SETPOINT_MAX):
            raise ValueError(
                f"Setpoint {value} °C is outside the allowed range "
                f"[{SETPOINT_MIN}, {SETPOINT_MAX}]."
            )
        self._write(f"out_sp_00 {value:.1f}")
        for attempt in range(3):
            time.sleep(0.05)
            try:
                confirmed_value = self.get_setpoint()
            except (TimeoutError, JulaboError) as exc:
                LOGGER.debug(
                    "Setpoint verify attempt %d failed: %s", attempt + 1, exc
                )
                if attempt == 2:
                    raise JulaboError(
                        "Setpoint state unknown after 3 verification attempts."
                    ) from exc
                continue
            if abs(confirmed_value - value) <= 0.05:
                return
            if attempt == 2:
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
        """Send a raw command and return the response."""

        return self._query(command)


def read_cached_port() -> str | None:
    """Return the cached serial port path if one was stored previously."""

    try:
        text = PORT_CACHE_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        LOGGER.debug("Could not read cached port: %s", exc)
        return None
    return text or None


def remember_port(port: str) -> None:
    """Persist the last working port for future runs."""

    try:
        PORT_CACHE_PATH.write_text(port, encoding="utf-8")
    except OSError as exc:
        LOGGER.debug("Could not write cached port: %s", exc)


def forget_port() -> bool:
    """Remove the cached serial port file. Returns True if it was removed."""
    try:
        PORT_CACHE_PATH.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        LOGGER.debug("Could not remove cached port: %s", exc)
        return False
    return True


def probe_port(port: str, timeout: float) -> bool:
    """Return ``True`` if the provided port responds to an identify command."""

    settings = SerialSettings(port=port, timeout=timeout)
    try:
        with JulaboChiller(settings) as chiller:
            chiller.identify()
    except (JulaboError, TimeoutError, serial.SerialException):
        return False
    else:
        remember_port(port)
        return True


def candidate_ports() -> Iterator[str]:
    """Yield candidate serial device paths for Julabo detection."""

    seen: set[str] = set()
    for port_info in list_ports.comports():
        if port_info.device and port_info.device not in seen:
            seen.add(port_info.device)
            yield port_info.device

    # Provide manual fallbacks for systems where ``list_ports`` returns an empty
    # list.  The logic is kept inline to avoid importing ``sys`` or ``glob`` at
    # module import time for environments that do not require them.
    import sys

    if sys.platform.startswith("win"):
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

    cached = read_cached_port()
    if cached and probe_port(cached, timeout):
        LOGGER.info("Using cached port %s", cached)
        return cached

    for port in candidate_ports():
        if port == cached:
            continue
        LOGGER.debug("Probing port %s", port)
        if probe_port(port, timeout):
            LOGGER.info("Detected Julabo chiller on %s", port)
            return port

    raise serial.SerialException(
        "Unable to automatically locate the Julabo chiller. "
        "Connect it and try again or specify --port explicitly."
    )
