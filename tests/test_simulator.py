"""Tests for julabo_control.simulator."""

from __future__ import annotations

import math
import sys
import time

import pytest

from julabo_control.core import SETPOINT_MAX, SETPOINT_MIN
from julabo_control.simulator import (
    FakeChillerBackend,
    FakeChillerState,
    SerialSimulator,
    _CommandParser,
)


class TestFakeChillerState:
    def test_initial_values(self) -> None:
        state = FakeChillerState()
        assert state.setpoint == 20.0
        assert state.temperature == 20.0
        assert state.running is False

    def test_drift_toward_setpoint_when_running(self) -> None:
        state = FakeChillerState(
            setpoint=30.0, temperature=20.0, running=True,
            drift_rate=10.0, noise_amplitude=0.0,
        )
        # Force a time gap
        state._last_update = time.monotonic() - 1.0
        state.update()
        # Should have moved significantly toward 30
        assert state.temperature > 25.0

    def test_drift_toward_ambient_when_stopped(self) -> None:
        state = FakeChillerState(
            setpoint=10.0, temperature=10.0, running=False,
            drift_rate=10.0, noise_amplitude=0.0,
        )
        state._last_update = time.monotonic() - 1.0
        state.update()
        # Should drift toward ambient (22°C), not setpoint
        assert state.temperature > 15.0

    def test_noise_within_bounds(self) -> None:
        """Noise should not deviate temperature wildly in a single step."""
        state = FakeChillerState(
            temperature=20.0, running=False,
            drift_rate=0.0, noise_amplitude=0.01,
        )
        state._last_update = time.monotonic()
        temps = []
        for _ in range(100):
            state.update()
            temps.append(state.temperature)
        # With drift_rate=0 the temperature should stay near 20 + noise
        assert all(abs(t - 20.0) < 1.0 for t in temps)

    def test_no_noise_when_zero(self) -> None:
        state = FakeChillerState(
            temperature=20.0, running=False,
            drift_rate=0.0, noise_amplitude=0.0,
        )
        state._last_update = time.monotonic()
        state.update()
        assert state.temperature == 20.0

    def test_exponential_approach(self) -> None:
        state = FakeChillerState(
            setpoint=30.0, temperature=20.0, running=True,
            drift_rate=1.0, noise_amplitude=0.0,
        )
        state._last_update = time.monotonic() - 1.0
        state.update()
        expected_rate = 1.0 - math.exp(-1.0)
        expected = 20.0 + 10.0 * expected_rate
        assert abs(state.temperature - expected) < 0.1


class TestFakeChillerBackend:
    def test_connect_close_noop(self) -> None:
        backend = FakeChillerBackend()
        backend.connect()
        backend.close()

    def test_context_manager(self) -> None:
        with FakeChillerBackend() as backend:
            assert backend.identify() == "JULABO FL1703 Simulator"

    def test_identify(self) -> None:
        backend = FakeChillerBackend()
        assert "Simulator" in backend.identify()

    def test_get_status(self) -> None:
        backend = FakeChillerBackend()
        assert backend.get_status() == "01 OK"

    def test_setpoint_round_trip(self) -> None:
        backend = FakeChillerBackend()
        backend.set_setpoint(25.0)
        assert backend.get_setpoint() == 25.0

    def test_setpoint_validation_low(self) -> None:
        backend = FakeChillerBackend()
        with pytest.raises(ValueError, match="outside the allowed range"):
            backend.set_setpoint(SETPOINT_MIN - 1)

    def test_setpoint_validation_high(self) -> None:
        backend = FakeChillerBackend()
        with pytest.raises(ValueError, match="outside the allowed range"):
            backend.set_setpoint(SETPOINT_MAX + 1)

    def test_setpoint_at_boundaries(self) -> None:
        backend = FakeChillerBackend()
        backend.set_setpoint(SETPOINT_MIN)
        assert backend.get_setpoint() == SETPOINT_MIN
        backend.set_setpoint(SETPOINT_MAX)
        assert backend.get_setpoint() == SETPOINT_MAX

    def test_temperature_returns_float(self) -> None:
        backend = FakeChillerBackend()
        temp = backend.get_temperature()
        assert isinstance(temp, float)

    def test_running_start_stop(self) -> None:
        backend = FakeChillerBackend()
        assert backend.is_running() is False
        assert backend.start() is True
        assert backend.is_running() is True
        assert backend.stop() is False
        assert backend.is_running() is False

    def test_set_running(self) -> None:
        backend = FakeChillerBackend()
        assert backend.set_running(True) is True
        assert backend.is_running() is True
        assert backend.set_running(False) is False
        assert backend.is_running() is False

    def test_raw_command_version(self) -> None:
        backend = FakeChillerBackend()
        assert "Simulator" in backend.raw_command("version")

    def test_raw_command_unknown(self) -> None:
        backend = FakeChillerBackend()
        result = backend.raw_command("bogus_cmd")
        assert "ERROR" in result


class TestCommandParser:
    def setup_method(self) -> None:
        self.state = FakeChillerState(noise_amplitude=0.0)
        self.parser = _CommandParser(self.state)

    def test_version(self) -> None:
        assert self.parser.parse("version") == self.state.identity

    def test_status(self) -> None:
        assert self.parser.parse("status") == "01 OK"

    def test_read_setpoint(self) -> None:
        self.state.setpoint = 25.50
        assert self.parser.parse("in_sp_00") == "25.50"

    def test_write_setpoint(self) -> None:
        self.parser.parse("out_sp_00 30.5")
        assert self.state.setpoint == 30.5

    def test_write_setpoint_invalid(self) -> None:
        result = self.parser.parse("out_sp_00 abc")
        assert "ERROR" in result

    def test_write_setpoint_out_of_range(self) -> None:
        result = self.parser.parse("out_sp_00 999")
        assert "ERROR" in result

    def test_read_temperature(self) -> None:
        self.state.temperature = 21.50
        result = self.parser.parse("in_pv_00")
        assert "21.50" in result

    def test_read_running(self) -> None:
        self.state.running = False
        assert self.parser.parse("in_mode_05") == "0"
        self.state.running = True
        assert self.parser.parse("in_mode_05") == "1"

    def test_write_running(self) -> None:
        self.parser.parse("out_mode_05 1")
        assert self.state.running is True
        self.parser.parse("out_mode_05 0")
        assert self.state.running is False

    def test_unknown_command(self) -> None:
        result = self.parser.parse("foo_bar")
        assert "ERROR" in result

    def test_empty_command(self) -> None:
        result = self.parser.parse("")
        assert "ERROR" in result


@pytest.mark.skipif(sys.platform == "win32", reason="PTY not available on Windows")
class TestSerialSimulator:
    def test_create_and_device_path(self) -> None:
        sim = SerialSimulator(noise=0.0)
        try:
            assert sim.device_path.startswith("/dev/")
        finally:
            sim.shutdown()

    def test_shutdown_clears_fds(self) -> None:
        sim = SerialSimulator()
        sim.shutdown()
        with pytest.raises(RuntimeError, match="shut down"):
            _ = sim.device_path

    def test_serve_and_communicate(self) -> None:
        """Start simulator in a thread, communicate via the PTY."""
        import threading

        sim = SerialSimulator(noise=0.0)
        device = sim.device_path
        thread = threading.Thread(target=sim.serve_forever, daemon=True)
        thread.start()

        try:
            # Open the slave side via pyserial
            import serial
            conn = serial.Serial(
                device, baudrate=4800, bytesize=serial.SEVENBITS,
                parity=serial.PARITY_EVEN, stopbits=serial.STOPBITS_ONE,
                timeout=2.0,
            )
            # Send version command
            conn.write(b"version\r\n")
            response = conn.readline().decode("ascii", errors="replace").strip()
            assert "Simulator" in response

            # Send read setpoint
            conn.write(b"in_sp_00\r\n")
            response = conn.readline().decode("ascii", errors="replace").strip()
            assert float(response) == 20.0

            conn.close()
        finally:
            sim.shutdown()
            thread.join(timeout=2.0)

    def test_windows_raises(self) -> None:
        """On Windows (or when mocked), should raise NotImplementedError."""
        import unittest.mock
        with unittest.mock.patch("julabo_control.simulator.os.name", "nt"):
            with pytest.raises(NotImplementedError, match="Windows"):
                SerialSimulator()

    def test_serve_forever_handles_read_error(self) -> None:
        """OSError on read should exit the serve loop gracefully."""
        import threading
        import warnings

        sim = SerialSimulator(noise=0.0)
        thread = threading.Thread(target=sim.serve_forever, daemon=True)
        thread.start()
        try:
            # Shut down the simulator, which closes FDs and sets _running=False
            time.sleep(0.1)
            sim.shutdown()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                thread.join(timeout=2.0)
            assert not thread.is_alive()
        finally:
            sim.shutdown()


class TestCommandParserWhitespace:
    def test_command_parser_whitespace_only_input(self) -> None:
        state = FakeChillerState(noise_amplitude=0.0)
        parser = _CommandParser(state)
        result = parser.parse("   ")
        assert "ERROR" in result
