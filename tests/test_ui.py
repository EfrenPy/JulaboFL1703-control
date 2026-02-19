"""Tests for julabo_control.ui."""

from __future__ import annotations

import logging
import tkinter as tk
from unittest.mock import MagicMock, patch

from julabo_control.ui import BaseChillerApp, _detect_font_size


class TestTemperatureHistoryPlot:
    def _make_plot(self, **kwargs):
        """Create a TemperatureHistoryPlot with mocked Tk/matplotlib."""
        with patch("julabo_control.ui.FigureCanvasTkAgg") as mock_canvas_cls, \
             patch("julabo_control.ui.Figure") as mock_figure_cls:
            mock_figure = MagicMock()
            mock_axes = MagicMock()
            mock_line = MagicMock()
            mock_axes.plot.return_value = (mock_line,)
            mock_figure.add_subplot.return_value = mock_axes
            mock_figure_cls.return_value = mock_figure

            mock_canvas = MagicMock()
            mock_canvas.get_tk_widget.return_value = MagicMock()
            mock_canvas_cls.return_value = mock_canvas

            from julabo_control.ui import TemperatureHistoryPlot

            master = MagicMock()
            plot = TemperatureHistoryPlot(master, **kwargs)
            plot._mock_line = mock_line
            return plot

    def test_record_adds_to_history(self) -> None:
        plot = self._make_plot()
        plot.record(20.0, timestamp=1000.0)
        plot.record(21.0, timestamp=1060.0)
        assert len(plot._history) == 2
        assert plot._history[0] == (1000.0, 20.0)

    def test_max_points_trimming(self) -> None:
        plot = self._make_plot(max_points=5)
        for i in range(10):
            plot.record(float(i), timestamp=float(i * 60))
        assert len(plot._history) == 5
        assert plot._history[0] == (300.0, 5.0)

    def test_clear(self) -> None:
        plot = self._make_plot()
        plot.record(20.0, timestamp=1000.0)
        plot.clear()
        assert len(plot._history) == 0

    def test_export_csv(self, tmp_path) -> None:
        plot = self._make_plot()
        plot.record(20.5, timestamp=1000.0)
        plot.record(21.0, timestamp=1060.0)
        csv_path = str(tmp_path / "output.csv")
        count = plot.export_csv(csv_path)
        assert count == 2

        with open(csv_path) as f:
            lines = f.readlines()
        assert lines[0].strip() == "timestamp_utc,elapsed_minutes,temperature_c"
        assert len(lines) == 3  # header + 2 data rows

    def test_history_property(self) -> None:
        plot = self._make_plot()
        plot.record(20.0, timestamp=1000.0)
        hist = plot.history
        assert len(hist) == 1
        # Verify it's a copy
        hist.clear()
        assert len(plot._history) == 1


def _make_base_app() -> BaseChillerApp:
    """Create a BaseChillerApp with mocked attributes."""
    obj = BaseChillerApp.__new__(BaseChillerApp)
    obj.root = MagicMock(spec=tk.Tk)
    obj.root.winfo_exists.return_value = True
    obj._refresh_job = None
    obj._flash_job = None
    obj.alarm = MagicMock()
    obj.temperature_plot = MagicMock()
    obj.temperature_logger = MagicMock()
    obj.main_frame = MagicMock()
    obj.temp_label = MagicMock()
    obj.toggle_button = MagicMock()
    obj.status_label = MagicMock()
    obj.status_var = MagicMock(spec=tk.StringVar)
    obj.running_var = MagicMock(spec=tk.BooleanVar)
    obj.poll_interval_var = MagicMock(spec=tk.IntVar)
    obj.alarm_threshold_var = MagicMock(spec=tk.DoubleVar)
    return obj


class TestOnAlarm:
    def test_sets_label_red_and_bells(self) -> None:
        app = _make_base_app()
        app._on_alarm()
        app.temp_label.configure.assert_called_with(fg="red")
        app.root.bell.assert_called_once()

    def test_starts_flash(self) -> None:
        app = _make_base_app()
        app._flash_job = None
        app._on_alarm()
        # Flash should have been started (root.after called)
        app.root.after.assert_called()


class TestOnClear:
    def test_sets_label_black(self) -> None:
        app = _make_base_app()
        app._flash_job = "after#1"
        app._on_clear()
        app.temp_label.configure.assert_called_with(fg="black")
        app.root.after_cancel.assert_called_with("after#1")
        assert app._flash_job is None


class TestStartStopFlash:
    def test_flash_calls_after(self) -> None:
        app = _make_base_app()
        app._flash_job = None
        app._start_flash()
        # root.after should be called for flash scheduling
        app.root.after.assert_called()

    def test_stop_flash_cancels(self) -> None:
        app = _make_base_app()
        app._flash_job = "job#1"
        app._stop_flash()
        app.root.after_cancel.assert_called_with("job#1")
        assert app._flash_job is None


class TestLogTemperature:
    def test_calls_logger_record(self) -> None:
        app = _make_base_app()
        app._log_temperature(21.5, 20.0)
        app.temperature_logger.record.assert_called_once_with(21.5, 20.0)

    def test_oserror_logged_at_debug(self, caplog) -> None:
        app = _make_base_app()
        app.temperature_logger.record.side_effect = OSError("disk full")
        with caplog.at_level(logging.DEBUG, logger="julabo_control.ui"):
            app._log_temperature(21.5, 20.0)
        assert "Temperature logging failed" in caplog.text

    def test_no_logger(self) -> None:
        app = _make_base_app()
        app.temperature_logger = None
        app._log_temperature(21.5, 20.0)  # should not raise


class TestExportCsv:
    def test_dialog_cancelled(self) -> None:
        app = _make_base_app()
        with patch("julabo_control.ui.filedialog") as mock_fd:
            mock_fd.asksaveasfilename.return_value = ""
            app.export_csv()
        app.temperature_plot.export_csv.assert_not_called()

    def test_valid_path(self) -> None:
        app = _make_base_app()
        app.temperature_plot.export_csv.return_value = 10
        with patch("julabo_control.ui.filedialog") as mock_fd:
            mock_fd.asksaveasfilename.return_value = "/tmp/out.csv"
            app.export_csv()
        app.temperature_plot.export_csv.assert_called_once_with("/tmp/out.csv")

    def test_no_plot(self) -> None:
        app = _make_base_app()
        app.temperature_plot = None
        app.export_csv()  # should not raise


class TestCancelRefresh:
    def test_cancels_job(self) -> None:
        app = _make_base_app()
        app._refresh_job = "job#5"
        app._cancel_refresh()
        app.root.after_cancel.assert_called_with("job#5")
        assert app._refresh_job is None

    def test_no_job(self) -> None:
        app = _make_base_app()
        app._refresh_job = None
        app._cancel_refresh()  # should not raise


class TestDetectFontSize:
    def test_96_dpi(self) -> None:
        mock_root = MagicMock()
        mock_root.winfo_fpixels.return_value = 96.0
        assert _detect_font_size(mock_root) == 12

    def test_144_dpi(self) -> None:
        mock_root = MagicMock()
        mock_root.winfo_fpixels.return_value = 144.0
        assert _detect_font_size(mock_root) == 18

    def test_192_dpi(self) -> None:
        mock_root = MagicMock()
        mock_root.winfo_fpixels.return_value = 192.0
        assert _detect_font_size(mock_root) == 24

    def test_very_high_dpi_clamped(self) -> None:
        mock_root = MagicMock()
        mock_root.winfo_fpixels.return_value = 384.0
        assert _detect_font_size(mock_root) == 24  # clamped to max

    def test_very_low_dpi_clamped(self) -> None:
        mock_root = MagicMock()
        mock_root.winfo_fpixels.return_value = 48.0
        assert _detect_font_size(mock_root) == 10  # clamped to min

    def test_fallback_when_root_none(self) -> None:
        with patch("julabo_control.ui.tk._default_root", None):
            assert _detect_font_size(None) == 12

    def test_fallback_on_tcl_error(self) -> None:
        mock_root = MagicMock()
        mock_root.winfo_fpixels.side_effect = tk.TclError("no display")
        assert _detect_font_size(mock_root) == 12

    def test_detect_font_size_attribute_error(self) -> None:
        mock_root = MagicMock()
        mock_root.winfo_fpixels.side_effect = AttributeError("missing attr")
        assert _detect_font_size(mock_root) == 12


class TestSetSchedule:
    def _make_plot(self):
        """Create a TemperatureHistoryPlot with mocked Tk/matplotlib."""
        with patch("julabo_control.ui.FigureCanvasTkAgg") as mock_canvas_cls, \
             patch("julabo_control.ui.Figure") as mock_figure_cls:
            mock_figure = MagicMock()
            mock_axes = MagicMock()
            mock_line = MagicMock()
            mock_schedule_line = MagicMock()
            mock_axes.plot.side_effect = [(mock_line,), (mock_schedule_line,)]
            mock_axes.axhline.return_value = MagicMock()
            mock_figure.add_subplot.return_value = mock_axes
            mock_figure_cls.return_value = mock_figure

            mock_canvas = MagicMock()
            mock_canvas.get_tk_widget.return_value = MagicMock()
            mock_canvas_cls.return_value = mock_canvas

            from julabo_control.ui import TemperatureHistoryPlot

            master = MagicMock()
            plot = TemperatureHistoryPlot(master)
            plot._mock_schedule_line = mock_schedule_line
            return plot

    def test_set_schedule_draws_line(self) -> None:
        plot = self._make_plot()
        from julabo_control.schedule import ScheduleStep, SetpointSchedule

        schedule = SetpointSchedule(steps=[
            ScheduleStep(0.0, 20.0),
            ScheduleStep(10.0, 30.0),
        ])
        plot.set_schedule(schedule)
        plot._schedule_line.set_data.assert_called()
        plot._schedule_line.set_visible.assert_called_with(True)

    def test_set_schedule_none_hides_line(self) -> None:
        plot = self._make_plot()
        plot.set_schedule(None)
        plot._schedule_line.set_visible.assert_called_with(False)


class TestUpdatePlotSinglePoint:
    def _make_plot(self):
        with patch("julabo_control.ui.FigureCanvasTkAgg") as mock_canvas_cls, \
             patch("julabo_control.ui.Figure") as mock_figure_cls:
            mock_figure = MagicMock()
            mock_axes = MagicMock()
            mock_line = MagicMock()
            mock_axes.plot.return_value = (mock_line,)
            mock_axes.axhline.return_value = MagicMock()
            mock_figure.add_subplot.return_value = mock_axes
            mock_figure_cls.return_value = mock_figure

            mock_canvas = MagicMock()
            mock_canvas.get_tk_widget.return_value = MagicMock()
            mock_canvas_cls.return_value = mock_canvas

            from julabo_control.ui import TemperatureHistoryPlot

            return TemperatureHistoryPlot(MagicMock())

    def test_update_plot_single_point(self) -> None:
        plot = self._make_plot()
        plot.record(20.0, timestamp=1000.0)
        # Single point should trigger the x_max fallback to 1.0
        plot.axes.set_xlim.assert_called()
        call_args = plot.axes.set_xlim.call_args[0]
        assert call_args[0] == 0.0
        assert call_args[1] >= 1.0
