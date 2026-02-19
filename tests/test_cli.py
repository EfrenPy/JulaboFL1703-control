"""Tests for julabo_control.cli."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from julabo_control.cli import main


@pytest.fixture
def _mock_chiller():
    """Patch auto_detect_port and JulaboChiller for CLI tests."""
    mock_chiller = MagicMock()
    mock_chiller.__enter__ = MagicMock(return_value=mock_chiller)
    mock_chiller.__exit__ = MagicMock(return_value=False)

    with patch("julabo_control.cli.auto_detect_port", return_value="/dev/ttyUSB0"), \
         patch("julabo_control.cli.JulaboChiller", return_value=mock_chiller), \
         patch("julabo_control.cli.remember_port"):
        yield mock_chiller


class TestCLI:
    def test_version_command(self, _mock_chiller, capsys) -> None:
        _mock_chiller.identify.return_value = "JULABO FL1703"
        result = main(["version"])
        assert result == 0
        captured = capsys.readouterr()
        assert "JULABO FL1703" in captured.out

    def test_set_setpoint_command(self, _mock_chiller, capsys) -> None:
        result = main(["set-setpoint", "25.0"])
        assert result == 0
        _mock_chiller.set_setpoint.assert_called_once_with(25.0)

    def test_send_subcommand(self, _mock_chiller, capsys) -> None:
        _mock_chiller.raw_command.return_value = "OK"
        result = main(["send", "in_sp_00"])
        assert result == 0
        _mock_chiller.raw_command.assert_called_once_with("in_sp_00")

    def test_missing_command_exits(self) -> None:
        with pytest.raises(SystemExit):
            main([])

    def test_get_temperature(self, _mock_chiller, capsys) -> None:
        _mock_chiller.get_temperature.return_value = 21.50
        result = main(["get-temperature"])
        assert result == 0
        captured = capsys.readouterr()
        assert "21.50" in captured.out

    def test_start_command(self, _mock_chiller, capsys) -> None:
        _mock_chiller.start.return_value = True
        result = main(["start"])
        assert result == 0
        captured = capsys.readouterr()
        assert "started" in captured.out.lower()

    def test_stop_command(self, _mock_chiller, capsys) -> None:
        _mock_chiller.stop.return_value = False
        result = main(["stop"])
        assert result == 0
        captured = capsys.readouterr()
        assert "stopped" in captured.out.lower()

    def test_status_command(self, _mock_chiller, capsys) -> None:
        _mock_chiller.get_status.return_value = "01 OK"
        result = main(["status"])
        assert result == 0
        captured = capsys.readouterr()
        assert "01 OK" in captured.out

    def test_get_setpoint_command(self, _mock_chiller, capsys) -> None:
        _mock_chiller.get_setpoint.return_value = 20.50
        result = main(["get-setpoint"])
        assert result == 0
        captured = capsys.readouterr()
        assert "20.50" in captured.out

    def test_explicit_port(self, capsys) -> None:
        mock_chiller = MagicMock()
        mock_chiller.__enter__ = MagicMock(return_value=mock_chiller)
        mock_chiller.__exit__ = MagicMock(return_value=False)
        mock_chiller.identify.return_value = "JULABO"
        with patch("julabo_control.cli.JulaboChiller", return_value=mock_chiller), \
             patch("julabo_control.cli.remember_port"):
            result = main(["--port", "/dev/ttyUSB1", "version"])
        assert result == 0

    def test_config_file_merge(self, tmp_path, capsys) -> None:
        config_file = tmp_path / "test.ini"
        config_file.write_text("[serial]\nport = /dev/ttyUSB99\ntimeout = 10.0\n")
        mock_chiller = MagicMock()
        mock_chiller.__enter__ = MagicMock(return_value=mock_chiller)
        mock_chiller.__exit__ = MagicMock(return_value=False)
        mock_chiller.get_status.return_value = "01 OK"
        with patch("julabo_control.cli.JulaboChiller", return_value=mock_chiller) as MockCls, \
             patch("julabo_control.cli.remember_port"):
            result = main(["--config", str(config_file), "status"])
        assert result == 0
        # Verify the config port was used
        call_args = MockCls.call_args[0][0]
        assert call_args.port == "/dev/ttyUSB99"
        assert call_args.timeout == 10.0


class TestVersion:
    def test_version_importable(self) -> None:
        from julabo_control import __version__

        assert isinstance(__version__, str)
        assert __version__ != "0.0.0"

    def test_version_flag(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        from julabo_control import __version__

        assert __version__ in captured.out


class TestForgetPortCLI:
    def test_forget_port_existing(self, tmp_path, monkeypatch, capsys) -> None:
        cache_file = tmp_path / "port_cache"
        cache_file.write_text("/dev/ttyUSB0")
        monkeypatch.setattr("julabo_control.core.PORT_CACHE_PATH", cache_file)
        result = main(["forget-port"])
        assert result == 0
        captured = capsys.readouterr()
        assert "removed" in captured.out.lower()

    def test_forget_port_missing(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            "julabo_control.core.PORT_CACHE_PATH", tmp_path / "nonexistent"
        )
        result = main(["forget-port"])
        assert result == 0
        captured = capsys.readouterr()
        assert "No cached port" in captured.out


class TestConfigureLogging:
    def test_verbose_adds_stream_handler(self) -> None:
        from julabo_control.cli import _configure_logging

        with patch("julabo_control.cli.logging.basicConfig") as mock_bc:
            _configure_logging(True)
            mock_bc.assert_called_once()
            handlers = mock_bc.call_args[1]["handlers"]
            assert any(isinstance(h, logging.StreamHandler) for h in handlers)

    def test_log_file_adds_file_handler(self, tmp_path) -> None:
        from julabo_control.cli import _configure_logging

        log_file = str(tmp_path / "test.log")
        with patch("julabo_control.cli.logging.basicConfig") as mock_bc:
            _configure_logging(False, log_file)
            mock_bc.assert_called_once()
            handlers = mock_bc.call_args[1]["handlers"]
            assert any(isinstance(h, logging.FileHandler) for h in handlers)

    def test_no_logging(self) -> None:
        from julabo_control.cli import _configure_logging

        with patch("julabo_control.cli.logging.basicConfig") as mock_bc:
            _configure_logging(False, None)
            mock_bc.assert_not_called()


class TestGuiSubcommand:
    def test_gui_with_port(self) -> None:
        with patch("julabo_control.cli.run_gui") as mock_gui, \
             patch("julabo_control.cli.remember_port"):
            result = main(["--port", "/dev/ttyUSB0", "gui"])
        assert result == 0
        mock_gui.assert_called_once()
        # Verify settings passed
        call_args = mock_gui.call_args
        settings = call_args[0][0]
        assert settings.port == "/dev/ttyUSB0"

    def test_gui_auto_detect_success(self) -> None:
        detect = patch("julabo_control.cli.auto_detect_port", return_value="/dev/ttyUSB0")
        with detect as mock_detect, \
             patch("julabo_control.cli.run_gui") as mock_gui:
            result = main(["gui"])
        assert result == 0
        mock_detect.assert_called_once()
        mock_gui.assert_called_once()

    def test_gui_auto_detect_failure(self) -> None:
        import serial

        exc = serial.SerialException("not found")
        with patch("julabo_control.cli.auto_detect_port", side_effect=exc), \
             patch("julabo_control.cli.run_gui") as mock_gui:
            result = main(["gui"])
        assert result == 2
        mock_gui.assert_called_once()
        # Should have been called with None settings and startup_error
        call_args = mock_gui.call_args
        assert call_args[0][0] is None
        assert call_args[1]["startup_error"] is not None

    def test_gui_with_all_options(self) -> None:
        with patch("julabo_control.cli.run_gui") as mock_gui, \
             patch("julabo_control.cli.remember_port"):
            result = main([
                "--port", "/dev/ttyUSB0",
                "gui",
                "--poll-interval", "3000",
                "--alarm-threshold", "5.0",
                "--temperature-log", "/tmp/temp.csv",
                "--alarm-log", "/tmp/alarm.csv",
                "--desktop-notifications",
                "--font-size", "16",
            ])
        assert result == 0
        call_kwargs = mock_gui.call_args[1]
        assert call_kwargs["poll_interval"] == 3000
        assert call_kwargs["alarm_threshold"] == 5.0
        assert call_kwargs["log_file"] == "/tmp/temp.csv"
        assert call_kwargs["alarm_log"] == "/tmp/alarm.csv"
        assert call_kwargs["desktop_notifications"] is True
        assert call_kwargs["font_size"] == 16

    def test_gui_font_size_from_config(self, tmp_path) -> None:
        config_file = tmp_path / "test.ini"
        config_file.write_text("[gui]\nfont_size = 14\n")
        with patch("julabo_control.cli.run_gui") as mock_gui, \
             patch("julabo_control.cli.remember_port"):
            result = main([
                "--port", "/dev/ttyUSB0",
                "--config", str(config_file),
                "gui",
            ])
        assert result == 0
        call_kwargs = mock_gui.call_args[1]
        assert call_kwargs["font_size"] == 14


class TestMonitorSubcommand:
    def _make_chiller_mock(self) -> MagicMock:
        mock_chiller = MagicMock()
        mock_chiller.__enter__ = MagicMock(return_value=mock_chiller)
        mock_chiller.__exit__ = MagicMock(return_value=False)
        mock_chiller.get_temperature.return_value = 21.5
        mock_chiller.get_setpoint.return_value = 20.0
        mock_chiller.is_running.return_value = True
        return mock_chiller

    def test_monitor_finite_count(self) -> None:
        mock_chiller = self._make_chiller_mock()

        with patch("julabo_control.cli.auto_detect_port", return_value="/dev/ttyUSB0"), \
             patch("julabo_control.cli.JulaboChiller", return_value=mock_chiller), \
             patch("julabo_control.cli.remember_port"), \
             patch("time.sleep"):
            result = main(["monitor", "--count", "2", "--no-overwrite"])
        assert result == 0

    def test_monitor_with_csv(self, tmp_path) -> None:
        csv_path = str(tmp_path / "out.csv")
        mock_chiller = self._make_chiller_mock()

        with patch("julabo_control.cli.auto_detect_port", return_value="/dev/ttyUSB0"), \
             patch("julabo_control.cli.JulaboChiller", return_value=mock_chiller), \
             patch("julabo_control.cli.remember_port"), \
             patch("time.sleep"):
            result = main(["monitor", "--count", "1", "--csv", csv_path])
        assert result == 0

    def test_monitor_overwrite_mode(self, capsys) -> None:
        mock_chiller = self._make_chiller_mock()

        with patch("julabo_control.cli.auto_detect_port", return_value="/dev/ttyUSB0"), \
             patch("julabo_control.cli.JulaboChiller", return_value=mock_chiller), \
             patch("julabo_control.cli.remember_port"), \
             patch("time.sleep"):
            result = main(["monitor", "--count", "1"])
        assert result == 0

    def test_monitor_read_error_recovery(self) -> None:
        mock_chiller = self._make_chiller_mock()
        # First call raises, second succeeds
        mock_chiller.get_temperature.side_effect = [TimeoutError("timeout"), 21.5]

        with patch("julabo_control.cli.auto_detect_port", return_value="/dev/ttyUSB0"), \
             patch("julabo_control.cli.JulaboChiller", return_value=mock_chiller), \
             patch("julabo_control.cli.remember_port"), \
             patch("time.sleep"):
            result = main(["monitor", "--count", "1", "--no-overwrite"])
        assert result == 0

    def test_monitor_keyboard_interrupt(self) -> None:
        mock_chiller = self._make_chiller_mock()
        mock_chiller.get_temperature.side_effect = KeyboardInterrupt

        with patch("julabo_control.cli.auto_detect_port", return_value="/dev/ttyUSB0"), \
             patch("julabo_control.cli.JulaboChiller", return_value=mock_chiller), \
             patch("julabo_control.cli.remember_port"), \
             patch("time.sleep"):
            result = main(["monitor", "--count", "1"])
        assert result == 0


class TestCommandErrorHandling:
    def test_julabo_error_calls_parser_error(self, _mock_chiller) -> None:
        from julabo_control.core import JulaboError

        _mock_chiller.identify.side_effect = JulaboError("bad response")
        with pytest.raises(SystemExit):
            main(["version"])

    def test_timeout_error(self, _mock_chiller) -> None:
        _mock_chiller.identify.side_effect = TimeoutError("timeout")
        with pytest.raises(SystemExit):
            main(["version"])

    def test_serial_exception(self, _mock_chiller) -> None:
        import serial

        _mock_chiller.identify.side_effect = serial.SerialException("port error")
        with pytest.raises(SystemExit):
            main(["version"])
