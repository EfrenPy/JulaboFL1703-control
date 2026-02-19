"""Tests for julabo_control.gui.ChillerApp."""

from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock, patch

import pytest

from julabo_control.core import SETPOINT_MAX, SerialSettings
from julabo_control.gui import ChillerApp


@pytest.fixture
def mock_chiller() -> MagicMock:
    chiller = MagicMock()
    chiller.get_setpoint.return_value = 20.0
    chiller.get_temperature.return_value = 21.0
    chiller.is_running.return_value = False
    chiller.settings = SerialSettings(port="/dev/null")
    return chiller


@pytest.fixture
def app(mock_chiller: MagicMock) -> ChillerApp:
    """Create a ChillerApp with a fully mocked Tk root and chiller."""
    with patch("julabo_control.gui.configure_default_fonts"), \
         patch("julabo_control.gui.TemperatureHistoryPlot") as MockPlot, \
         patch("julabo_control.gui.remember_port"):
        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot

        root = MagicMock(spec=tk.Tk)
        root.winfo_exists.return_value = True

        settings = SerialSettings(port="/dev/null")
        obj = ChillerApp.__new__(ChillerApp)

        # Manually initialise enough state to make methods testable
        obj.root = root
        obj._chiller = mock_chiller
        obj._current_settings = settings
        obj._refresh_job = None
        obj._flash_job = None
        obj._timeout_value = settings.timeout
        obj._reconnect_delay = 1.0
        obj._reconnect_delay_max = 30.0
        obj._reconnect_delay_factor = 2.0
        obj._closed = False
        obj._schedule_runner = None
        obj.temperature_logger = None

        obj.port_var = MagicMock(spec=tk.StringVar)
        obj.entry_var = MagicMock(spec=tk.StringVar)
        obj.setpoint_var = MagicMock(spec=tk.StringVar)
        obj.temp_var = MagicMock(spec=tk.StringVar)
        obj.status_var = MagicMock(spec=tk.StringVar)
        obj.running_var = MagicMock(spec=tk.BooleanVar)
        obj.running_var.get.return_value = False
        obj.poll_interval_var = MagicMock(spec=tk.IntVar)
        obj.poll_interval_var.get.return_value = 5000
        obj.alarm_threshold_var = MagicMock(spec=tk.DoubleVar)
        obj.alarm_threshold_var.get.return_value = 2.0

        obj.temperature_plot = mock_plot
        obj.status_label = MagicMock()
        obj.temp_label = MagicMock()
        obj.toggle_button = MagicMock()
        obj.main_frame = MagicMock()

        obj.alarm = MagicMock()

        return obj


class TestRefreshReadings:
    def test_success(self, app: ChillerApp, mock_chiller: MagicMock) -> None:
        app.refresh_readings()
        app.setpoint_var.set.assert_called_with("20.00 \u00b0C")
        app.temp_var.set.assert_called_with("21.00 \u00b0C")
        app.running_var.set.assert_called_with(False)
        app.alarm.check.assert_called_once_with(21.0, 20.0)
        app.temperature_plot.record.assert_called_once_with(21.0)

    def test_error_triggers_reconnect(
        self, app: ChillerApp, mock_chiller: MagicMock
    ) -> None:
        mock_chiller.get_setpoint.side_effect = TimeoutError("comm error")
        with patch("julabo_control.gui.JulaboChiller") as MockChiller:
            new_chiller = MagicMock()
            MockChiller.return_value = new_chiller
            app.refresh_readings()
            MockChiller.assert_called_once()
            new_chiller.connect.assert_called_once()

    def test_reconnect_fails(
        self, app: ChillerApp, mock_chiller: MagicMock
    ) -> None:
        mock_chiller.get_setpoint.side_effect = TimeoutError("comm error")
        with patch("julabo_control.gui.JulaboChiller") as MockChiller:
            MockChiller.return_value.connect.side_effect = OSError("still broken")
            app.refresh_readings()
            assert app._chiller is None

    def test_no_chiller(self, app: ChillerApp) -> None:
        app._chiller = None
        app.refresh_readings()
        app.status_var.set.assert_called_with("Not connected to Julabo chiller.")


class TestApplySetpoint:
    def test_valid(self, app: ChillerApp, mock_chiller: MagicMock) -> None:
        app.entry_var.get.return_value = "25.0"
        app.apply_setpoint()
        mock_chiller.set_setpoint.assert_called_once_with(25.0)

    def test_invalid_text(self, app: ChillerApp) -> None:
        app.entry_var.get.return_value = "abc"
        app.apply_setpoint()
        app.status_var.set.assert_called_with("Invalid temperature value.")

    def test_out_of_range(self, app: ChillerApp) -> None:
        app.entry_var.get.return_value = str(SETPOINT_MAX + 10)
        app.apply_setpoint()
        assert "between" in app.status_var.set.call_args[0][0]

    def test_not_connected(self, app: ChillerApp) -> None:
        app._chiller = None
        app.entry_var.get.return_value = "20"
        app.apply_setpoint()
        app.status_var.set.assert_called_with("Not connected to Julabo chiller.")


class TestToggleRunning:
    def test_start(self, app: ChillerApp, mock_chiller: MagicMock) -> None:
        app.running_var.get.return_value = False
        mock_chiller.set_running.return_value = True
        app.toggle_running()
        mock_chiller.set_running.assert_called_once_with(True)
        app.running_var.set.assert_called_with(True)

    def test_stop(self, app: ChillerApp, mock_chiller: MagicMock) -> None:
        app.running_var.get.return_value = True
        mock_chiller.set_running.return_value = False
        app.toggle_running()
        mock_chiller.set_running.assert_called_once_with(False)
        app.running_var.set.assert_called_with(False)


class TestOnClose:
    def test_cleanup(self, app: ChillerApp, mock_chiller: MagicMock) -> None:
        app._refresh_job = "after#1"
        app._flash_job = "after#2"
        app.on_close()
        app.root.after_cancel.assert_any_call("after#1")
        app.root.after_cancel.assert_any_call("after#2")
        mock_chiller.close.assert_called_once()
        app.root.destroy.assert_called_once()

    def test_on_close_with_logger(self, app: ChillerApp) -> None:
        mock_logger = MagicMock()
        app.temperature_logger = mock_logger
        app.on_close()
        mock_logger.close.assert_called_once()

    def test_on_close_with_schedule(self, app: ChillerApp) -> None:
        mock_runner = MagicMock()
        app._schedule_runner = mock_runner
        app.on_close()
        mock_runner.stop.assert_called_once()
        assert app._schedule_runner is None


class TestBackoff:
    def test_backoff_increases_on_failure(
        self, app: ChillerApp, mock_chiller: MagicMock
    ) -> None:
        mock_chiller.get_setpoint.side_effect = TimeoutError("comm error")
        with patch("julabo_control.gui.JulaboChiller") as MockChiller:
            MockChiller.return_value.connect.side_effect = OSError("still broken")
            initial_delay = app._reconnect_delay
            app.refresh_readings()
            assert app._reconnect_delay == initial_delay * app._reconnect_delay_factor

    def test_backoff_resets_on_success(
        self, app: ChillerApp, mock_chiller: MagicMock
    ) -> None:
        app._reconnect_delay = 16.0
        app.refresh_readings()
        assert app._reconnect_delay == 1.0


class TestToggleRunningEdgeCases:
    def test_not_connected(self, app: ChillerApp) -> None:
        app._chiller = None
        app.toggle_running()
        app.status_var.set.assert_called_with("Not connected to Julabo chiller.")


class TestOnCloseGuard:
    def test_double_close_is_safe(self, app: ChillerApp, mock_chiller: MagicMock) -> None:
        app.on_close()
        app.on_close()  # second call should be a no-op
        mock_chiller.close.assert_called_once()
        app.root.destroy.assert_called_once()


class TestScheduleMethods:
    def test_stop_schedule_no_runner(self, app: ChillerApp) -> None:
        """stop_schedule should be safe when no schedule is active."""
        app._schedule_runner = None
        app.stop_schedule()
        # Just verify no error

    def test_stop_schedule_active(self, app: ChillerApp) -> None:
        mock_runner = MagicMock()
        app._schedule_runner = mock_runner
        app.stop_schedule()
        mock_runner.stop.assert_called_once()
        assert app._schedule_runner is None

    def test_tick_schedule_no_runner(self, app: ChillerApp) -> None:
        app._schedule_runner = None
        app._tick_schedule()  # should not raise

    def test_tick_schedule_finished(self, app: ChillerApp) -> None:
        mock_runner = MagicMock()
        mock_runner.is_running = True
        mock_runner.is_finished = True
        mock_runner.tick.return_value = 20.0
        app._schedule_runner = mock_runner
        app._tick_schedule()
        assert app._schedule_runner is None
        app.temperature_plot.set_schedule.assert_called_with(None)

    def test_tick_schedule_shows_progress(self, app: ChillerApp) -> None:
        mock_runner = MagicMock()
        mock_runner.is_running = True
        mock_runner.is_finished = False
        mock_runner.elapsed_minutes = 5.0
        mock_runner.schedule.duration_minutes = 10.0
        mock_runner.tick.return_value = 25.0
        app._schedule_runner = mock_runner
        app._tick_schedule()
        msg = app.status_var.set.call_args[0][0]
        assert "5.0/10.0 min" in msg
        assert "50%" in msg

    def test_tick_schedule_exception_stops_runner(self, app: ChillerApp) -> None:
        mock_runner = MagicMock()
        mock_runner.is_running = True
        mock_runner.tick.side_effect = OSError("comm failure")
        app._schedule_runner = mock_runner
        app._tick_schedule()
        mock_runner.stop.assert_called_once()
        assert app._schedule_runner is None
        assert "Schedule error" in app.status_var.set.call_args[0][0]

    def test_load_schedule_not_connected(self, app: ChillerApp) -> None:
        app._chiller = None
        with patch("julabo_control.gui.filedialog") as mock_fd:
            mock_fd.askopenfilename.return_value = "/tmp/sched.csv"
            with patch("julabo_control.gui.SetpointSchedule") as MockSched:
                MockSched.load_csv.return_value = MagicMock()
                app.load_schedule()
        app.status_var.set.assert_called()
        assert "Not connected" in app.status_var.set.call_args[0][0]


class TestTestConnection:
    def test_empty_port_warning(self, app: ChillerApp) -> None:
        app.port_var.get.return_value = ""
        with patch("julabo_control.gui.messagebox") as mock_mb:
            app.test_connection()
            mock_mb.showwarning.assert_called_once()

    def test_success_path(self, app: ChillerApp, mock_chiller: MagicMock) -> None:
        app.port_var.get.return_value = "/dev/ttyUSB0"
        with patch("julabo_control.gui.JulaboChiller") as MockChiller, \
             patch("julabo_control.gui.remember_port"):
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_conn = MagicMock()
            mock_conn.get_setpoint.return_value = 20.0
            mock_conn.get_temperature.return_value = 21.0
            mock_conn.is_running.return_value = False
            MockChiller.side_effect = [mock_ctx, mock_conn]
            app.test_connection()
            mock_conn.connect.assert_called_once()


class TestLoadScheduleSuccess:
    def test_file_dialog_success(self, app: ChillerApp) -> None:
        mock_schedule = MagicMock()
        mock_schedule.steps = [MagicMock()]
        mock_schedule.duration_minutes = 10.0
        with patch("julabo_control.gui.filedialog") as mock_fd, \
             patch("julabo_control.gui.SetpointSchedule") as MockSched:
            mock_fd.askopenfilename.return_value = "/tmp/sched.csv"
            MockSched.load_csv.return_value = mock_schedule
            app.load_schedule()
        assert app._schedule_runner is not None
        app.temperature_plot.set_schedule.assert_called_with(mock_schedule)
        msg = app.status_var.set.call_args[0][0]
        assert "Schedule loaded" in msg

    def test_file_dialog_cancelled(self, app: ChillerApp) -> None:
        with patch("julabo_control.gui.filedialog") as mock_fd:
            mock_fd.askopenfilename.return_value = ""
            app.load_schedule()
        assert app._schedule_runner is None


# ---------------------------------------------------------------------------
# Additional tests for uncovered lines
# ---------------------------------------------------------------------------


class TestChillerAppInit:
    """Tests for ChillerApp.__init__ (lines 44-103)."""

    @patch("julabo_control.gui.TemperatureAlarm")
    @patch("julabo_control.gui.TemperatureHistoryPlot")
    @patch("julabo_control.gui.configure_default_fonts")
    def test_init_with_settings(
        self, mock_fonts: MagicMock, MockPlot: MagicMock, MockAlarm: MagicMock
    ) -> None:
        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot

        root = MagicMock(spec=tk.Tk)
        settings = SerialSettings(port="/dev/null")

        with patch.object(ChillerApp, "_build_layout"), \
             patch.object(ChillerApp, "_bind_shortcuts"), \
             patch.object(ChillerApp, "_center_window"), \
             patch.object(ChillerApp, "_show_status"), \
             patch.object(ChillerApp, "_attempt_connection", return_value=None) as mock_attempt, \
             patch.object(ChillerApp, "set_connected"), \
             patch("julabo_control.gui.tk.StringVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.BooleanVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.IntVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.DoubleVar", return_value=MagicMock()):
            app = ChillerApp(root, settings)

        mock_attempt.assert_called_once()
        assert app._reconnect_delay == 1.0
        assert app._closed is False
        assert app._current_settings is settings
        assert app.root is root

    @patch("julabo_control.gui.TemperatureAlarm")
    @patch("julabo_control.gui.TemperatureHistoryPlot")
    @patch("julabo_control.gui.configure_default_fonts")
    def test_init_without_settings(
        self, mock_fonts: MagicMock, MockPlot: MagicMock, MockAlarm: MagicMock
    ) -> None:
        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot

        root = MagicMock(spec=tk.Tk)

        with patch.object(ChillerApp, "_build_layout"), \
             patch.object(ChillerApp, "_bind_shortcuts"), \
             patch.object(ChillerApp, "_center_window"), \
             patch.object(ChillerApp, "_show_status"), \
             patch.object(ChillerApp, "set_connected") as mock_set, \
             patch("julabo_control.gui.tk.StringVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.BooleanVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.IntVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.DoubleVar", return_value=MagicMock()):
            app = ChillerApp(root, None)

        assert app._chiller is None
        mock_set.assert_called_once_with(None, None)

    @patch("julabo_control.gui.TemperatureAlarm")
    @patch("julabo_control.gui.TemperatureHistoryPlot")
    @patch("julabo_control.gui.configure_default_fonts")
    def test_init_with_startup_error(
        self, mock_fonts: MagicMock, MockPlot: MagicMock, MockAlarm: MagicMock
    ) -> None:
        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot

        root = MagicMock(spec=tk.Tk)
        settings = SerialSettings(port="/dev/null")
        err = RuntimeError("fail")

        with patch.object(ChillerApp, "_build_layout"), \
             patch.object(ChillerApp, "_bind_shortcuts"), \
             patch.object(ChillerApp, "_center_window"), \
             patch.object(ChillerApp, "_show_status"), \
             patch.object(ChillerApp, "set_connected"), \
             patch("julabo_control.gui.tk.StringVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.BooleanVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.IntVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.DoubleVar", return_value=MagicMock()):
            ChillerApp(root, settings, startup_error=err)

        # startup_error bypasses _attempt_connection; root.after schedules error
        root.after.assert_called()
        # The first after call should schedule the error display (arg 0 = 0)
        after_calls = [c for c in root.after.call_args_list if c[0][0] == 0]
        assert len(after_calls) >= 1

    @patch("julabo_control.gui.TemperatureFileLogger")
    @patch("julabo_control.gui.TemperatureAlarm")
    @patch("julabo_control.gui.TemperatureHistoryPlot")
    @patch("julabo_control.gui.configure_default_fonts")
    def test_init_with_log_file(
        self,
        mock_fonts: MagicMock,
        MockPlot: MagicMock,
        MockAlarm: MagicMock,
        MockLogger: MagicMock,
    ) -> None:
        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot

        root = MagicMock(spec=tk.Tk)
        settings = SerialSettings(port="/dev/null")

        with patch.object(ChillerApp, "_build_layout"), \
             patch.object(ChillerApp, "_bind_shortcuts"), \
             patch.object(ChillerApp, "_center_window"), \
             patch.object(ChillerApp, "_show_status"), \
             patch.object(ChillerApp, "_attempt_connection", return_value=None), \
             patch.object(ChillerApp, "set_connected"), \
             patch("julabo_control.gui.tk.StringVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.BooleanVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.IntVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.DoubleVar", return_value=MagicMock()):
            app = ChillerApp(root, settings, log_file="/tmp/test.csv")

        MockLogger.assert_called_once_with("/tmp/test.csv")
        assert app.temperature_logger is MockLogger.return_value

    @patch("julabo_control.gui.TemperatureAlarm")
    @patch("julabo_control.gui.TemperatureHistoryPlot")
    @patch("julabo_control.gui.configure_default_fonts")
    def test_init_successful_connection_sets_chiller(
        self, mock_fonts: MagicMock, MockPlot: MagicMock, MockAlarm: MagicMock
    ) -> None:
        """When _attempt_connection succeeds and sets _chiller, the success branch runs."""
        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot

        root = MagicMock(spec=tk.Tk)
        settings = SerialSettings(port="/dev/null")

        mock_chiller = MagicMock()

        def fake_attempt(self_inner: ChillerApp) -> None:
            self_inner._chiller = mock_chiller
            return None  # type: ignore[return-value]

        with patch.object(ChillerApp, "_build_layout"), \
             patch.object(ChillerApp, "_bind_shortcuts"), \
             patch.object(ChillerApp, "_center_window"), \
             patch.object(ChillerApp, "_show_status") as mock_show, \
             patch.object(ChillerApp, "_attempt_connection", fake_attempt), \
             patch.object(ChillerApp, "set_connected") as mock_set, \
             patch("julabo_control.gui.tk.StringVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.BooleanVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.IntVar", return_value=MagicMock()), \
             patch("julabo_control.gui.tk.DoubleVar", return_value=MagicMock()):
            ChillerApp(root, settings)

        # Should call set_connected with the chiller and settings
        mock_set.assert_called_once_with(mock_chiller, settings)
        # Should call _show_status with empty string and black colour
        mock_show.assert_called_once_with("", color="black")


class TestAttemptConnection:
    """Tests for _attempt_connection (lines 109-117)."""

    def test_success(self) -> None:
        app = ChillerApp.__new__(ChillerApp)
        app._current_settings = SerialSettings(port="/dev/null")
        app._chiller = None

        with patch("julabo_control.gui.JulaboChiller") as MockChiller, \
             patch("julabo_control.gui.remember_port") as mock_remember:
            mock_chiller = MagicMock()
            MockChiller.return_value = mock_chiller
            result = app._attempt_connection()

        assert result is None
        mock_chiller.connect.assert_called_once()
        mock_remember.assert_called_once_with("/dev/null")
        assert app._chiller is mock_chiller

    def test_failure(self) -> None:
        app = ChillerApp.__new__(ChillerApp)
        app._current_settings = SerialSettings(port="/dev/null")
        app._chiller = None

        with patch("julabo_control.gui.JulaboChiller") as MockChiller, \
             patch("julabo_control.gui.remember_port"):
            mock_chiller = MagicMock()
            mock_chiller.connect.side_effect = OSError("no device")
            MockChiller.return_value = mock_chiller
            result = app._attempt_connection()

        assert isinstance(result, OSError)
        assert app._chiller is None


class TestSetConnectedExtended:
    """Additional tests for set_connected (line 134 clear_temperature_plot)."""

    def test_new_chiller_closes_previous(
        self, app: ChillerApp, mock_chiller: MagicMock
    ) -> None:
        new_chiller = MagicMock()
        new_chiller.get_setpoint.return_value = 22.0
        new_chiller.get_temperature.return_value = 23.0
        new_chiller.is_running.return_value = False
        new_settings = SerialSettings(port="/dev/ttyUSB1")
        with patch("julabo_control.gui.remember_port"):
            app.set_connected(new_chiller, new_settings)
        mock_chiller.close.assert_called_once()
        assert app._chiller is new_chiller

    def test_same_chiller_no_close(
        self, app: ChillerApp, mock_chiller: MagicMock
    ) -> None:
        with patch("julabo_control.gui.remember_port"):
            app.set_connected(mock_chiller, SerialSettings(port="/dev/null"))
        mock_chiller.close.assert_not_called()

    def test_none_clears_plot(
        self, app: ChillerApp, mock_chiller: MagicMock
    ) -> None:
        """Setting chiller to None should close the previous chiller and clear the plot."""
        app.set_connected(None, None)
        mock_chiller.close.assert_called_once()
        assert app._chiller is None
        app.temperature_plot.clear.assert_called()


class TestCenterWindow:
    """Tests for _center_window (lines 467-475)."""

    def test_eval_success(self, app: ChillerApp) -> None:
        # tk.Tk.eval is inherited from tk.Misc; spec may not expose it
        app.root.eval = MagicMock()
        app._center_window()
        app.root.update_idletasks.assert_called_once()
        app.root.eval.assert_called_once()

    def test_eval_tcl_error_fallback(self, app: ChillerApp) -> None:
        app.root.eval = MagicMock(side_effect=tk.TclError("no PlaceWindow"))
        app.root.winfo_width.return_value = 800
        app.root.winfo_height.return_value = 600
        app.root.winfo_screenwidth.return_value = 1920
        app.root.winfo_screenheight.return_value = 1080
        app._center_window()
        app.root.geometry.assert_called_once()
        # Verify the geometry string encodes the centre position
        geom_arg = app.root.geometry.call_args[0][0]
        assert geom_arg == "+560+240"


class TestBindShortcuts:
    """Tests for _bind_shortcuts (lines 461-463)."""

    def test_bindings_registered(self, app: ChillerApp) -> None:
        app._bind_shortcuts()
        calls = [c[0][0] for c in app.root.bind.call_args_list]
        assert "<Control-r>" in calls
        assert "<Control-s>" in calls
        assert "<Escape>" in calls

    def test_ctrl_r_calls_refresh(self, app: ChillerApp) -> None:
        app._bind_shortcuts()
        # Find the callback for <Control-r> and invoke it
        for call in app.root.bind.call_args_list:
            if call[0][0] == "<Control-r>":
                callback = call[0][1]
                with patch.object(app, "refresh_readings") as mock_refresh:
                    callback(None)
                    mock_refresh.assert_called_once()
                break


class TestBuildLayout:
    """Tests for _build_layout (lines 357-457)."""

    @patch("julabo_control.gui.TemperatureHistoryPlot")
    @patch("julabo_control.gui.tk.LabelFrame")
    @patch("julabo_control.gui.tk.Spinbox")
    @patch("julabo_control.gui.tk.Entry")
    @patch("julabo_control.gui.tk.Button")
    @patch("julabo_control.gui.tk.Label")
    @patch("julabo_control.gui.tk.Frame")
    def test_build_layout_creates_widgets(
        self,
        MockFrame: MagicMock,
        MockLabel: MagicMock,
        MockButton: MagicMock,
        MockEntry: MagicMock,
        MockSpinbox: MagicMock,
        MockLabelFrame: MagicMock,
        MockPlot: MagicMock,
    ) -> None:
        app = ChillerApp.__new__(ChillerApp)
        app.root = MagicMock(spec=tk.Tk)
        app.port_var = MagicMock()
        app.entry_var = MagicMock()
        app.setpoint_var = MagicMock()
        app.temp_var = MagicMock()
        app.status_var = MagicMock()
        app.running_var = MagicMock()
        app.poll_interval_var = MagicMock()
        app.alarm_threshold_var = MagicMock()

        mock_child1 = MagicMock()
        mock_child2 = MagicMock()
        mock_frame = MagicMock()
        mock_frame.winfo_children.return_value = [mock_child1, mock_child2]
        MockFrame.return_value = mock_frame

        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot

        app._build_layout()

        # main_frame was created and packed
        MockFrame.assert_called_once()
        mock_frame.pack.assert_called_once()
        # temperature_plot was created
        assert app.temperature_plot is mock_plot
        mock_plot.widget.pack.assert_called_once()
        # Labels, buttons, entries were created
        assert MockLabel.call_count >= 5
        assert MockButton.call_count >= 4


class TestRunGui:
    """Tests for run_gui (lines 507-522)."""

    @patch("julabo_control.gui.ChillerApp")
    @patch("julabo_control.gui.tk.Tk")
    def test_launches_mainloop(self, MockTk: MagicMock, MockApp: MagicMock) -> None:
        from julabo_control.gui import run_gui

        mock_root = MagicMock()
        MockTk.return_value = mock_root
        mock_app = MagicMock()
        MockApp.return_value = mock_app

        run_gui(SerialSettings(port="/dev/null"))

        mock_root.mainloop.assert_called_once()
        mock_app.on_close.assert_called_once()

    @patch("julabo_control.gui.ChillerApp")
    @patch("julabo_control.gui.tk.Tk")
    def test_on_close_called_on_exception(
        self, MockTk: MagicMock, MockApp: MagicMock
    ) -> None:
        from julabo_control.gui import run_gui

        mock_root = MagicMock()
        MockTk.return_value = mock_root
        mock_app = MagicMock()
        MockApp.return_value = mock_app
        mock_root.mainloop.side_effect = KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            run_gui(SerialSettings(port="/dev/null"))

        mock_app.on_close.assert_called_once()

    @patch("julabo_control.gui.ChillerApp")
    @patch("julabo_control.gui.tk.Tk")
    def test_passes_all_kwargs(self, MockTk: MagicMock, MockApp: MagicMock) -> None:
        from julabo_control.gui import run_gui

        mock_root = MagicMock()
        MockTk.return_value = mock_root

        settings = SerialSettings(port="/dev/null")
        err = RuntimeError("test")

        run_gui(
            settings,
            startup_error=err,
            poll_interval=3000,
            alarm_threshold=5.0,
            log_file="/tmp/log.csv",
            alarm_log="/tmp/alarm.csv",
            desktop_notifications=True,
            font_size=14,
        )

        MockApp.assert_called_once_with(
            mock_root,
            settings,
            startup_error=err,
            poll_interval=3000,
            alarm_threshold=5.0,
            log_file="/tmp/log.csv",
            alarm_log="/tmp/alarm.csv",
            desktop_notifications=True,
            font_size=14,
        )


class TestScheduleApplySetpointError:
    """Tests for _schedule_apply_setpoint error path (lines 318-321)."""

    def test_error_logged(self, app: ChillerApp, mock_chiller: MagicMock) -> None:
        mock_chiller.set_setpoint.side_effect = TimeoutError("timeout")
        # Should not raise, just log
        app._schedule_apply_setpoint(25.0)
        mock_chiller.set_setpoint.assert_called_once_with(25.0)

    def test_no_chiller_returns_early(self, app: ChillerApp) -> None:
        app._chiller = None
        # Should not raise
        app._schedule_apply_setpoint(25.0)


class TestRefreshReadingsCloseError:
    """Tests for close-error suppression during reconnect (lines 187-188)."""

    def test_close_error_suppressed(
        self, app: ChillerApp, mock_chiller: MagicMock
    ) -> None:
        """When chiller.close() raises during reconnect, it should be suppressed."""
        mock_chiller.get_setpoint.side_effect = TimeoutError("comm error")
        mock_chiller.close.side_effect = OSError("close failed")
        with patch("julabo_control.gui.JulaboChiller") as MockChiller:
            new_chiller = MagicMock()
            MockChiller.return_value = new_chiller
            app.refresh_readings()
            # Should still attempt reconnect despite close error
            MockChiller.assert_called_once()
            new_chiller.connect.assert_called_once()
