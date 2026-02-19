"""FakeChiller simulator for development and testing without hardware."""

from __future__ import annotations

import argparse
import logging
import math
import os
import random
import select
import time
from dataclasses import dataclass, field

from .core import SETPOINT_MAX, SETPOINT_MIN

LOGGER = logging.getLogger(__name__)

_AMBIENT = 22.0


@dataclass
class FakeChillerState:
    """Internal simulation state for a fake Julabo chiller."""

    setpoint: float = 20.0
    temperature: float = 20.0
    running: bool = False
    identity: str = "JULABO FL1703 Simulator"
    status_code: str = "01 OK"
    drift_rate: float = 0.1
    noise_amplitude: float = 0.02
    _last_update: float = field(default_factory=time.monotonic)

    def update(self) -> None:
        """Advance the simulation: temperature approaches setpoint or ambient."""
        now = time.monotonic()
        dt = now - self._last_update
        self._last_update = now

        if self.running:
            target = self.setpoint
        else:
            target = _AMBIENT

        diff = target - self.temperature
        # Exponential approach: temperature moves toward target
        rate = 1.0 - math.exp(-self.drift_rate * dt)
        self.temperature += diff * rate

        # Add Gaussian noise
        if self.noise_amplitude > 0:
            self.temperature += random.gauss(0, self.noise_amplitude)


class FakeChillerBackend:
    """Duck-typed replacement for :class:`JulaboChiller` using simulated state."""

    def __init__(
        self,
        *,
        initial_temp: float = 20.0,
        drift_rate: float = 0.1,
        noise: float = 0.02,
    ) -> None:
        self.state = FakeChillerState(
            temperature=initial_temp,
            drift_rate=drift_rate,
            noise_amplitude=noise,
        )

    def connect(self) -> None:
        """No-op for the simulator."""

    def close(self) -> None:
        """No-op for the simulator."""

    def __enter__(self) -> FakeChillerBackend:
        self.connect()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def identify(self) -> str:
        return self.state.identity

    def get_status(self) -> str:
        return self.state.status_code

    def get_setpoint(self) -> float:
        return self.state.setpoint

    def set_setpoint(self, value: float) -> None:
        if not (SETPOINT_MIN <= value <= SETPOINT_MAX):
            raise ValueError(
                f"Setpoint {value} °C is outside the allowed range "
                f"[{SETPOINT_MIN}, {SETPOINT_MAX}]."
            )
        self.state.setpoint = value

    def get_temperature(self) -> float:
        self.state.update()
        return self.state.temperature

    def is_running(self) -> bool:
        return self.state.running

    def set_running(self, start: bool) -> bool:
        self.state.running = start
        return self.state.running

    def start(self) -> bool:
        return self.set_running(True)

    def stop(self) -> bool:
        return self.set_running(False)

    def raw_command(self, cmd: str) -> str:
        return _CommandParser(self.state).parse(cmd)


class _CommandParser:
    """Maps raw Julabo ASCII commands to state methods."""

    def __init__(self, state: FakeChillerState) -> None:
        self._state = state

    def parse(self, line: str) -> str:
        parts = line.strip().split(None, 1)
        if not parts:
            return "ERROR: empty command"
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "version":
            return self._state.identity
        elif cmd == "status":
            return self._state.status_code
        elif cmd == "in_sp_00":
            return f"{self._state.setpoint:.2f}"
        elif cmd == "out_sp_00":
            try:
                value = float(arg)
            except ValueError:
                return "ERROR: invalid setpoint value"
            if not (SETPOINT_MIN <= value <= SETPOINT_MAX):
                return "ERROR: setpoint out of range"
            self._state.setpoint = value
            return ""
        elif cmd == "in_pv_00":
            self._state.update()
            return f"{self._state.temperature:.2f}"
        elif cmd == "in_mode_05":
            return "1" if self._state.running else "0"
        elif cmd == "out_mode_05":
            self._state.running = arg.strip() == "1"
            return ""
        else:
            return f"ERROR: unknown command '{cmd}'"


class SerialSimulator:
    """PTY-based serial emulator that responds to Julabo ASCII commands.

    Unix only. Uses ``os.openpty()`` to create a master/slave pair.
    Connect a real ``JulaboChiller`` to :attr:`device_path` for testing.
    """

    def __init__(
        self,
        *,
        initial_temp: float = 20.0,
        drift_rate: float = 0.1,
        noise: float = 0.02,
    ) -> None:
        if os.name == "nt":
            raise NotImplementedError(
                "SerialSimulator requires Unix PTY support. "
                "Use FakeChillerBackend on Windows instead."
            )
        self._state = FakeChillerState(
            temperature=initial_temp,
            drift_rate=drift_rate,
            noise_amplitude=noise,
        )
        self._parser = _CommandParser(self._state)
        self._master_fd: int | None = None
        self._slave_fd: int | None = None
        self._running = False

        master, slave = os.openpty()
        self._master_fd = master
        self._slave_fd = slave

        # Configure the slave TTY to match Julabo serial settings
        try:
            import termios

            attrs = termios.tcgetattr(slave)
            # Set baud rate to 4800
            attrs[4] = termios.B4800  # ispeed
            attrs[5] = termios.B4800  # ospeed
            # 7 data bits, even parity, 1 stop bit
            attrs[2] &= ~termios.CSIZE
            attrs[2] |= termios.CS7
            attrs[2] |= termios.PARENB
            attrs[2] &= ~termios.PARODD
            # RTS/CTS flow control
            attrs[2] |= termios.CRTSCTS
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
        except (ImportError, termios.error):
            LOGGER.debug("Could not configure PTY terminal attributes")

    @property
    def device_path(self) -> str:
        """Return the slave TTY path clients should connect to."""
        if self._slave_fd is None:
            raise RuntimeError("Simulator has been shut down")
        return os.ttyname(self._slave_fd)

    def serve_forever(self) -> None:
        """Read commands from the PTY and write responses until shutdown."""
        if self._master_fd is None:
            raise RuntimeError("Simulator has been shut down")
        self._running = True
        buf = b""
        while self._running:
            master = self._master_fd
            if master is None:
                break
            ready, _, _ = select.select([master], [], [], 0.1)
            if not ready:
                continue
            try:
                data = os.read(master, 4096)
            except OSError:
                break
            if not data:
                break
            buf += data
            while b"\r" in buf or b"\n" in buf:
                # Split on first CR or LF
                for sep in (b"\r\n", b"\r", b"\n"):
                    idx = buf.find(sep)
                    if idx >= 0:
                        line = buf[:idx].decode("ascii", errors="replace").strip()
                        buf = buf[idx + len(sep) :]
                        break
                else:
                    break  # pragma: no cover
                if not line:
                    continue
                response = self._parser.parse(line)
                if response and master is not None:
                    try:
                        os.write(master, (response + "\r\n").encode("ascii"))
                    except OSError:
                        break

    def shutdown(self) -> None:
        """Stop serving and close PTY file descriptors."""
        self._running = False
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
        if self._slave_fd is not None:
            try:
                os.close(self._slave_fd)
            except OSError:
                pass
            self._slave_fd = None


def main() -> None:  # pragma: no cover - CLI helper
    """Run the Julabo serial simulator."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--initial-temp",
        type=float,
        default=20.0,
        help="Initial temperature in °C (default: 20.0)",
    )
    parser.add_argument(
        "--drift-rate",
        type=float,
        default=0.1,
        help="Temperature drift rate in °C/s (default: 0.1)",
    )
    parser.add_argument(
        "--noise",
        type=float,
        default=0.02,
        help="Gaussian noise amplitude in °C (default: 0.02)",
    )
    parser.add_argument(
        "--port",
        default=None,
        help="Create a symlink at this path pointing to the PTY device",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    sim = SerialSimulator(
        initial_temp=args.initial_temp,
        drift_rate=args.drift_rate,
        noise=args.noise,
    )
    device = sim.device_path
    if args.port:
        try:
            os.symlink(device, args.port)
            LOGGER.info("Symlink created: %s -> %s", args.port, device)
        except OSError as exc:
            LOGGER.warning("Could not create symlink: %s", exc)

    LOGGER.info("Simulator listening on %s", device)
    LOGGER.info("Press Ctrl+C to stop")
    try:
        sim.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sim.shutdown()
        if args.port:
            try:
                os.unlink(args.port)
            except OSError:
                pass


if __name__ == "__main__":  # pragma: no cover
    main()
