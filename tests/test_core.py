"""Tests for julabo_control.core."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from julabo_control.core import (
    SETPOINT_MAX,
    SETPOINT_MIN,
    ChillerBackend,
    JulaboChiller,
    JulaboError,
    SerialSettings,
    auto_detect_port,
    candidate_ports,
    forget_port,
    probe_port,
    read_cached_port,
    remember_port,
)
from julabo_control.simulator import FakeChillerBackend

from .conftest import MockSerial


class TestJulaboChiller:
    def test_identify(self, chiller: JulaboChiller, mock_serial: MockSerial) -> None:
        mock_serial.queue("JULABO FL1703")
        result = chiller.identify()
        assert result == "JULABO FL1703"
        assert mock_serial.last_command == "version"

    def test_get_setpoint(self, chiller: JulaboChiller, mock_serial: MockSerial) -> None:
        mock_serial.queue("20.00")
        result = chiller.get_setpoint()
        assert result == 20.0
        assert mock_serial.last_command == "in_sp_00"

    def test_get_temperature(self, chiller: JulaboChiller, mock_serial: MockSerial) -> None:
        mock_serial.queue("21.50")
        result = chiller.get_temperature()
        assert result == 21.5
        assert mock_serial.last_command == "in_pv_00"

    def test_set_setpoint_success(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        mock_serial.queue("25.00")  # read-back from get_setpoint (first verify attempt)
        with patch("julabo_control.core.time.sleep"):
            chiller.set_setpoint(25.0)
        assert b"out_sp_00 25.0" in mock_serial.written[0]

    def test_set_setpoint_tolerance_failure(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        # Queue 3 bad read-backs (one for each retry)
        mock_serial.queue("26.00", "26.00", "26.00")
        with patch("julabo_control.core.time.sleep"):
            with pytest.raises(JulaboError, match="did not acknowledge"):
                chiller.set_setpoint(25.0)

    def test_set_setpoint_within_tolerance(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        mock_serial.queue("25.04")  # within 0.05 tolerance
        with patch("julabo_control.core.time.sleep"):
            chiller.set_setpoint(25.0)  # should not raise

    def test_set_setpoint_all_retries_timeout(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        """All 3 verify attempts time out → state unknown error."""
        # Queue nothing — all 3 reads will timeout (b"" → TimeoutError)
        with patch("julabo_control.core.time.sleep"):
            with pytest.raises(JulaboError, match="state unknown"):
                chiller.set_setpoint(25.0)

    def test_set_setpoint_verify_sleep_called(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        """Verify that 50ms sleep is called before each verify attempt."""
        mock_serial.queue("25.00")
        with patch("julabo_control.core.time.sleep") as mock_sleep:
            chiller.set_setpoint(25.0)
        # At least one 0.05 sleep call for verification
        calls = [c for c in mock_sleep.call_args_list if c[0] == (0.05,)]
        assert len(calls) >= 1

    def test_set_running_start(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        mock_serial.queue("1")  # is_running confirmation
        result = chiller.set_running(True)
        assert result is True
        assert b"out_mode_05 1" in mock_serial.written[0]

    def test_set_running_mismatch(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        mock_serial.queue("0")  # confirmation doesn't match
        with pytest.raises(JulaboError, match="did not acknowledge"):
            chiller.set_running(True)

    def test_is_running_true(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        mock_serial.queue("1")
        assert chiller.is_running() is True

    def test_is_running_false(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        mock_serial.queue("0")
        assert chiller.is_running() is False

    def test_error_response(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        mock_serial.queue("Error: bad command")
        with pytest.raises(JulaboError):
            chiller.identify()

    def test_timeout_empty_readline(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        # No responses queued → empty readline → TimeoutError
        with pytest.raises(TimeoutError):
            chiller.identify()

    def test_serial_property_not_connected(self) -> None:
        settings = SerialSettings(port="/dev/null")
        obj = JulaboChiller(settings)
        with pytest.raises(RuntimeError, match="Call connect"):
            _ = obj.serial

    def test_raw_command(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        mock_serial.queue("OK")
        result = chiller.raw_command("in_sp_00")
        assert result == "OK"
        assert mock_serial.last_command == "in_sp_00"

    def test_get_status(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        mock_serial.queue("01 OK")
        result = chiller.get_status()
        assert result == "01 OK"

    def test_start_convenience(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        mock_serial.queue("1")
        assert chiller.start() is True

    def test_stop_convenience(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        mock_serial.queue("0")
        assert chiller.stop() is False


class TestRateLimit:
    def test_sleep_called_when_rapid(self) -> None:
        settings = SerialSettings(port="/dev/null")
        chiller = JulaboChiller(settings)
        chiller._serial = MockSerial()
        chiller._serial.queue("JULABO FL1703", "JULABO FL1703")

        # Each _enforce_rate_limit calls monotonic twice: once for now, once for _last update
        # First identify: now=10.0, elapsed=10.0-0.0=10.0 > 0.1, no sleep. _last=10.0
        # Second identify: now=10.05, elapsed=10.05-10.0=0.05 < 0.1, SLEEP. _last=10.1
        with patch("julabo_control.core.time.sleep") as mock_sleep, \
             patch("julabo_control.core.time.monotonic", side_effect=[10.0, 10.0, 10.05, 10.1]):
            chiller.identify()
            chiller.identify()
            mock_sleep.assert_called()

    def test_no_sleep_when_enough_time(self) -> None:
        settings = SerialSettings(port="/dev/null")
        chiller = JulaboChiller(settings)
        chiller._serial = MockSerial()
        chiller._serial.queue("JULABO FL1703", "JULABO FL1703")

        # First identify: now=10.0, elapsed=10.0-0.0=10.0 > 0.1, no sleep. _last=10.0
        # Second identify: now=20.0, elapsed=20.0-10.0=10.0 > 0.1, no sleep. _last=20.0
        with patch("julabo_control.core.time.sleep") as mock_sleep, \
             patch("julabo_control.core.time.monotonic", side_effect=[10.0, 10.0, 20.0, 20.0]):
            chiller.identify()
            chiller.identify()
            mock_sleep.assert_not_called()


class TestPortCaching:
    def test_read_cached_port_missing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "julabo_control.core.PORT_CACHE_PATH", tmp_path / "nonexistent"
        )
        assert read_cached_port() is None

    def test_remember_and_read(self, tmp_path, monkeypatch) -> None:
        cache_file = tmp_path / "port_cache"
        monkeypatch.setattr("julabo_control.core.PORT_CACHE_PATH", cache_file)
        remember_port("/dev/ttyUSB0")
        assert read_cached_port() == "/dev/ttyUSB0"

    def test_read_empty_file(self, tmp_path, monkeypatch) -> None:
        cache_file = tmp_path / "port_cache"
        cache_file.write_text("")
        monkeypatch.setattr("julabo_control.core.PORT_CACHE_PATH", cache_file)
        assert read_cached_port() is None

    def test_read_cached_port_debug_log(self, tmp_path, monkeypatch, caplog) -> None:
        monkeypatch.setattr(
            "julabo_control.core.PORT_CACHE_PATH", tmp_path / "nonexistent"
        )
        with caplog.at_level(logging.DEBUG, logger="julabo_control.core"):
            read_cached_port()
        assert "Could not read cached port" in caplog.text

    def test_remember_port_debug_log(self, tmp_path, monkeypatch, caplog) -> None:
        # Point to a read-only directory to trigger OSError
        bad_path = tmp_path / "nodir" / "port"
        monkeypatch.setattr("julabo_control.core.PORT_CACHE_PATH", bad_path)
        with caplog.at_level(logging.DEBUG, logger="julabo_control.core"):
            remember_port("/dev/ttyUSB0")
        assert "Could not write cached port" in caplog.text


class TestForgetPort:
    def test_forget_existing(self, tmp_path, monkeypatch) -> None:
        cache_file = tmp_path / "port_cache"
        cache_file.write_text("/dev/ttyUSB0")
        monkeypatch.setattr("julabo_control.core.PORT_CACHE_PATH", cache_file)
        assert forget_port() is True
        assert not cache_file.exists()

    def test_forget_missing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "julabo_control.core.PORT_CACHE_PATH", tmp_path / "nonexistent"
        )
        assert forget_port() is False

    def test_forget_error(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("julabo_control.core.PORT_CACHE_PATH", tmp_path / "nodir" / "port")
        with patch("julabo_control.core.PORT_CACHE_PATH") as mock_path:
            mock_path.unlink.side_effect = OSError("permission denied")
            assert forget_port() is False


class TestSetpointValidation:
    def test_set_setpoint_below_min(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        with pytest.raises(ValueError, match="outside the allowed range"):
            chiller.set_setpoint(SETPOINT_MIN - 1)

    def test_set_setpoint_above_max(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        with pytest.raises(ValueError, match="outside the allowed range"):
            chiller.set_setpoint(SETPOINT_MAX + 1)

    def test_set_setpoint_at_min_boundary(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        mock_serial.queue(f"{SETPOINT_MIN:.2f}")
        with patch("julabo_control.core.time.sleep"):
            chiller.set_setpoint(SETPOINT_MIN)  # should not raise

    def test_set_setpoint_at_max_boundary(
        self, chiller: JulaboChiller, mock_serial: MockSerial
    ) -> None:
        mock_serial.queue(f"{SETPOINT_MAX:.2f}")
        with patch("julabo_control.core.time.sleep"):
            chiller.set_setpoint(SETPOINT_MAX)  # should not raise


class TestConnectClose:
    def test_connect_creates_serial(self) -> None:
        settings = SerialSettings(port="/dev/null")
        chiller = JulaboChiller(settings)
        with patch("julabo_control.core.serial.Serial") as mock_serial_cls:
            chiller.connect()
            mock_serial_cls.assert_called_once_with(
                port="/dev/null",
                baudrate=settings.baudrate,
                timeout=settings.timeout,
                bytesize=settings.bytesize,
                parity=settings.parity,
                stopbits=settings.stopbits,
                rtscts=settings.rtscts,
            )

    def test_connect_idempotent(self) -> None:
        settings = SerialSettings(port="/dev/null")
        chiller = JulaboChiller(settings)
        mock_ser = MagicMock()
        chiller._serial = mock_ser
        with patch("julabo_control.core.serial.Serial") as mock_serial_cls:
            chiller.connect()  # should not create a new one
            mock_serial_cls.assert_not_called()

    def test_close_closes_serial(self) -> None:
        settings = SerialSettings(port="/dev/null")
        chiller = JulaboChiller(settings)
        mock_ser = MagicMock()
        chiller._serial = mock_ser
        chiller.close()
        mock_ser.close.assert_called_once()
        assert chiller._serial is None

    def test_close_when_not_connected(self) -> None:
        settings = SerialSettings(port="/dev/null")
        chiller = JulaboChiller(settings)
        chiller.close()  # should not raise

    def test_settings_property(self) -> None:
        settings = SerialSettings(port="/dev/ttyUSB0")
        chiller = JulaboChiller(settings)
        assert chiller.settings is settings


class TestProbePort:
    def test_probe_success(self) -> None:
        with patch("julabo_control.core.JulaboChiller") as mock_chiller_cls, \
             patch("julabo_control.core.remember_port") as mock_remember:
            mock_ctx = MagicMock()
            mock_chiller_cls.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_chiller_cls.return_value.__exit__ = MagicMock(return_value=False)
            result = probe_port("/dev/ttyUSB0", 2.0)
        assert result is True
        mock_remember.assert_called_once_with("/dev/ttyUSB0")

    def test_probe_failure_timeout(self) -> None:
        with patch("julabo_control.core.JulaboChiller") as mock_chiller_cls:
            mock_ctx = MagicMock()
            mock_ctx.identify.side_effect = TimeoutError("timeout")
            mock_chiller_cls.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_chiller_cls.return_value.__exit__ = MagicMock(return_value=False)
            result = probe_port("/dev/ttyUSB0", 2.0)
        assert result is False

    def test_probe_failure_serial_exception(self) -> None:
        import serial as serial_mod

        with patch("julabo_control.core.JulaboChiller") as mock_chiller_cls:
            mock_chiller_cls.return_value.__enter__ = MagicMock(
                side_effect=serial_mod.SerialException("open failed")
            )
            mock_chiller_cls.return_value.__exit__ = MagicMock(return_value=False)
            result = probe_port("/dev/ttyUSB0", 2.0)
        assert result is False

    def test_probe_failure_julabo_error(self) -> None:
        with patch("julabo_control.core.JulaboChiller") as mock_chiller_cls:
            mock_ctx = MagicMock()
            mock_ctx.identify.side_effect = JulaboError("bad response")
            mock_chiller_cls.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_chiller_cls.return_value.__exit__ = MagicMock(return_value=False)
            result = probe_port("/dev/ttyUSB0", 2.0)
        assert result is False


class TestCandidatePorts:
    def test_comports_results(self) -> None:
        mock_port = MagicMock()
        mock_port.device = "/dev/ttyUSB0"
        with patch("julabo_control.core.list_ports.comports", return_value=[mock_port]), \
             patch("sys.platform", "linux"), \
             patch("glob.glob", return_value=[]):
            ports = list(candidate_ports())
        assert "/dev/ttyUSB0" in ports

    def test_windows_fallback(self) -> None:
        with patch("julabo_control.core.list_ports.comports", return_value=[]), \
             patch("sys.platform", "win32"):
            ports = list(candidate_ports())
        assert "COM1" in ports
        assert len(ports) == 256

    def test_linux_fallback(self) -> None:
        with patch("julabo_control.core.list_ports.comports", return_value=[]), \
             patch("sys.platform", "linux"), \
             patch(
                 "glob.glob",
                 side_effect=lambda p: ["/dev/ttyUSB0"] if "USB" in p else [],
             ):
            ports = list(candidate_ports())
        assert "/dev/ttyUSB0" in ports

    def test_deduplication(self) -> None:
        p1 = MagicMock()
        p1.device = "/dev/ttyUSB0"
        p2 = MagicMock()
        p2.device = "/dev/ttyUSB0"  # duplicate
        with patch("julabo_control.core.list_ports.comports", return_value=[p1, p2]), \
             patch("sys.platform", "linux"), \
             patch("glob.glob", return_value=[]):
            ports = list(candidate_ports())
        assert ports.count("/dev/ttyUSB0") == 1


class TestAutoDetectPort:
    def test_cached_port_works(self) -> None:
        with patch("julabo_control.core.read_cached_port", return_value="/dev/ttyUSB0"), \
             patch("julabo_control.core.probe_port", return_value=True):
            result = auto_detect_port(2.0)
        assert result == "/dev/ttyUSB0"

    def test_cached_stale_falls_back(self) -> None:
        with patch("julabo_control.core.read_cached_port", return_value="/dev/ttyUSB0"), \
             patch(
                 "julabo_control.core.probe_port",
                 side_effect=lambda p, t: p == "/dev/ttyUSB1",
             ), \
             patch(
                 "julabo_control.core.candidate_ports",
                 return_value=["/dev/ttyUSB0", "/dev/ttyUSB1"],
             ):
            result = auto_detect_port(2.0)
        assert result == "/dev/ttyUSB1"

    def test_nothing_found_raises(self) -> None:
        import serial as serial_mod

        with patch("julabo_control.core.read_cached_port", return_value=None), \
             patch("julabo_control.core.probe_port", return_value=False), \
             patch(
                 "julabo_control.core.candidate_ports",
                 return_value=["/dev/ttyUSB0"],
             ):
            with pytest.raises(
                serial_mod.SerialException, match="Unable to automatically"
            ):
                auto_detect_port(2.0)

    def test_no_cached_port(self) -> None:
        with patch("julabo_control.core.read_cached_port", return_value=None), \
             patch("julabo_control.core.probe_port", return_value=True), \
             patch(
                 "julabo_control.core.candidate_ports",
                 return_value=["/dev/ttyUSB0"],
             ):
            result = auto_detect_port(2.0)
        assert result == "/dev/ttyUSB0"

    def test_cached_port_skipped_in_scan(self) -> None:
        """When cached port fails, it should be skipped in the candidate scan."""
        call_args: list[str] = []

        def track_probe(port: str, timeout: float) -> bool:
            call_args.append(port)
            return port == "/dev/ttyUSB1"

        with patch("julabo_control.core.read_cached_port", return_value="/dev/ttyUSB0"), \
             patch("julabo_control.core.probe_port", side_effect=track_probe), \
             patch(
                 "julabo_control.core.candidate_ports",
                 return_value=["/dev/ttyUSB0", "/dev/ttyUSB1"],
             ):
            result = auto_detect_port(2.0)
        assert result == "/dev/ttyUSB1"
        # /dev/ttyUSB0 should appear only once (the cached attempt), not again in scan
        assert call_args.count("/dev/ttyUSB0") == 1


class TestChillerBackendProtocol:
    def test_julabo_chiller_satisfies_protocol(self) -> None:
        """JulaboChiller must be a structural subtype of ChillerBackend."""
        settings = SerialSettings(port="/dev/null")
        chiller = JulaboChiller(settings)
        assert isinstance(chiller, ChillerBackend)

    def test_fake_chiller_backend_satisfies_protocol(self) -> None:
        """FakeChillerBackend must satisfy the ChillerBackend protocol."""
        fake = FakeChillerBackend()
        assert isinstance(fake, ChillerBackend)

    def test_custom_protocol_compliant_backend(self) -> None:
        """A minimal custom class that implements all methods satisfies Protocol."""

        class MinimalBackend:
            def connect(self) -> None: pass
            def close(self) -> None: pass
            def identify(self) -> str: return "test"
            def get_status(self) -> str: return "ok"
            def get_setpoint(self) -> float: return 0.0
            def set_setpoint(self, value: float) -> None: pass
            def get_temperature(self) -> float: return 0.0
            def is_running(self) -> bool: return False
            def set_running(self, start: bool) -> bool: return False
            def start(self) -> bool: return False
            def stop(self) -> bool: return False

        backend = MinimalBackend()
        assert isinstance(backend, ChillerBackend)
