"""Tests for julabo_control.alarm."""

from __future__ import annotations

import csv

from julabo_control.alarm import _LOG_HEADER, TemperatureAlarm


class TestTemperatureAlarm:
    def test_alarm_triggers(self) -> None:
        triggered = []
        alarm = TemperatureAlarm(threshold=2.0, on_alarm=lambda: triggered.append("alarm"))
        result = alarm.check(25.0, 20.0)
        assert result is True
        assert alarm.is_alarming is True
        assert triggered == ["alarm"]

    def test_alarm_clears(self) -> None:
        cleared = []
        alarm = TemperatureAlarm(
            threshold=2.0,
            on_alarm=lambda: None,
            on_clear=lambda: cleared.append("clear"),
        )
        alarm.check(25.0, 20.0)  # trigger
        assert alarm.is_alarming is True
        alarm.check(20.5, 20.0)  # within threshold
        assert alarm.is_alarming is False
        assert cleared == ["clear"]

    def test_no_repeated_callbacks(self) -> None:
        triggered = []
        alarm = TemperatureAlarm(threshold=2.0, on_alarm=lambda: triggered.append(1))
        alarm.check(25.0, 20.0)  # trigger
        alarm.check(26.0, 20.0)  # still alarming, no re-trigger
        alarm.check(27.0, 20.0)  # still alarming
        assert len(triggered) == 1

    def test_disabled_with_zero_threshold(self) -> None:
        triggered = []
        alarm = TemperatureAlarm(threshold=0.0, on_alarm=lambda: triggered.append(1))
        result = alarm.check(100.0, 20.0)
        assert result is False
        assert alarm.is_alarming is False
        assert triggered == []

    def test_alarm_at_exact_threshold_does_not_trigger(self) -> None:
        alarm = TemperatureAlarm(threshold=2.0)
        result = alarm.check(22.0, 20.0)  # deviation == threshold, not >
        assert result is False

    def test_no_callbacks(self) -> None:
        alarm = TemperatureAlarm(threshold=2.0)
        alarm.check(25.0, 20.0)
        assert alarm.is_alarming is True
        alarm.check(20.0, 20.0)
        assert alarm.is_alarming is False

    def test_disable_clears_active_alarm(self) -> None:
        cleared = []
        alarm = TemperatureAlarm(
            threshold=2.0,
            on_clear=lambda: cleared.append("clear"),
        )
        alarm.check(25.0, 20.0)
        assert alarm.is_alarming is True
        alarm.threshold = 0.0
        alarm.check(25.0, 20.0)
        assert alarm.is_alarming is False
        assert cleared == ["clear"]


class TestAlarmLogging:
    def test_file_created_on_trigger(self, tmp_path) -> None:
        log_path = str(tmp_path / "alarm.csv")
        alarm = TemperatureAlarm(threshold=2.0, log_file=log_path)
        alarm.check(25.0, 20.0)  # trigger
        with open(log_path) as f:
            reader = list(csv.reader(f))
        assert reader[0] == _LOG_HEADER
        assert reader[1][1] == "ALARM"
        alarm.close()

    def test_clear_row_appended(self, tmp_path) -> None:
        log_path = str(tmp_path / "alarm.csv")
        alarm = TemperatureAlarm(threshold=2.0, log_file=log_path)
        alarm.check(25.0, 20.0)  # trigger
        alarm.check(20.5, 20.0)  # clear
        with open(log_path) as f:
            reader = list(csv.reader(f))
        assert len(reader) == 3  # header + ALARM + CLEAR
        assert reader[2][1] == "CLEAR"
        alarm.close()

    def test_no_file_when_none(self) -> None:
        alarm = TemperatureAlarm(threshold=2.0, log_file=None)
        alarm.check(25.0, 20.0)
        alarm.close()

    def test_header_written_once(self, tmp_path) -> None:
        log_path = str(tmp_path / "alarm.csv")
        alarm = TemperatureAlarm(threshold=2.0, log_file=log_path)
        alarm.check(25.0, 20.0)
        alarm.close()
        # Re-open and trigger again
        alarm2 = TemperatureAlarm(threshold=2.0, log_file=log_path)
        alarm2.check(30.0, 20.0)
        with open(log_path) as f:
            reader = list(csv.reader(f))
        # Header should appear only once
        headers = [r for r in reader if r == _LOG_HEADER]
        assert len(headers) == 1
        alarm2.close()

    def test_close_is_safe(self, tmp_path) -> None:
        log_path = str(tmp_path / "alarm.csv")
        alarm = TemperatureAlarm(threshold=2.0, log_file=log_path)
        alarm.close()  # close without opening
        alarm.close()  # double close
