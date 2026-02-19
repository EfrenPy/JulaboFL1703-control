"""Tests for julabo_control.temperature_logger."""

from __future__ import annotations

from pathlib import Path

from julabo_control.temperature_logger import TemperatureFileLogger


class TestTemperatureFileLogger:
    def test_creates_file_with_header(self, tmp_path: Path) -> None:
        log_path = tmp_path / "temps.csv"
        logger = TemperatureFileLogger(log_path)
        logger.record(20.5, 21.0, timestamp=1000.0)
        logger.close()

        lines = log_path.read_text().strip().splitlines()
        assert lines[0] == "timestamp_utc,elapsed_minutes,temperature_c,setpoint_c"
        assert "20.50" in lines[1]
        assert "21.00" in lines[1]

    def test_appends_rows(self, tmp_path: Path) -> None:
        log_path = tmp_path / "temps.csv"
        logger = TemperatureFileLogger(log_path)
        logger.record(20.0, 21.0, timestamp=1000.0)
        logger.record(20.5, 21.0, timestamp=1060.0)
        logger.close()

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 3  # header + 2 data rows

    def test_elapsed_minutes(self, tmp_path: Path) -> None:
        log_path = tmp_path / "temps.csv"
        logger = TemperatureFileLogger(log_path)
        logger.record(20.0, 21.0, timestamp=1000.0)
        logger.record(20.5, 21.0, timestamp=1120.0)  # 2 minutes later
        logger.close()

        lines = log_path.read_text().strip().splitlines()
        # First row: elapsed 0.00
        assert ",0.00," in lines[1]
        # Second row: elapsed 2.00
        assert ",2.00," in lines[2]

    def test_no_duplicate_header(self, tmp_path: Path) -> None:
        log_path = tmp_path / "temps.csv"
        logger1 = TemperatureFileLogger(log_path)
        logger1.record(20.0, 21.0, timestamp=1000.0)
        logger1.close()

        # Open again — should not re-write header
        logger2 = TemperatureFileLogger(log_path)
        logger2.record(20.5, 21.0, timestamp=1060.0)
        logger2.close()

        content = log_path.read_text()
        assert content.count("timestamp_utc") == 1

    def test_close_resets_state(self, tmp_path: Path) -> None:
        log_path = tmp_path / "temps.csv"
        logger = TemperatureFileLogger(log_path)
        logger.record(20.0, 21.0, timestamp=1000.0)
        logger.close()
        assert logger._start_time is None
        assert logger._fh is None
        assert logger._writer is None

    def test_path_property(self, tmp_path: Path) -> None:
        log_path = tmp_path / "temps.csv"
        logger = TemperatureFileLogger(log_path)
        assert logger.path == log_path

    def test_context_manager(self, tmp_path: Path) -> None:
        log_path = tmp_path / "temps.csv"
        with TemperatureFileLogger(log_path) as logger:
            logger.record(20.0, 21.0, timestamp=1000.0)
            logger.record(20.5, 21.0, timestamp=1060.0)

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 3

    def test_file_handle_reused(self, tmp_path: Path) -> None:
        log_path = tmp_path / "temps.csv"
        logger = TemperatureFileLogger(log_path)
        logger.record(20.0, 21.0, timestamp=1000.0)
        fh1 = logger._fh
        logger.record(20.5, 21.0, timestamp=1060.0)
        fh2 = logger._fh
        assert fh1 is fh2
        logger.close()

    def test_double_close_safe(self, tmp_path: Path) -> None:
        log_path = tmp_path / "temps.csv"
        logger = TemperatureFileLogger(log_path)
        logger.record(20.0, 21.0, timestamp=1000.0)
        logger.close()
        logger.close()  # should not raise

    def test_data_flushed_before_close(self, tmp_path: Path) -> None:
        log_path = tmp_path / "temps.csv"
        logger = TemperatureFileLogger(log_path)
        logger.record(20.0, 21.0, timestamp=1000.0)
        # Data should be readable even before close (due to flush)
        content = log_path.read_text()
        assert "20.00" in content
        logger.close()
