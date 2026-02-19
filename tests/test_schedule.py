"""Tests for julabo_control.schedule."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from julabo_control.schedule import (
    ScheduleRunner,
    ScheduleStep,
    SetpointSchedule,
)


class TestSetpointSchedule:
    def test_setpoint_at_before_first(self) -> None:
        schedule = SetpointSchedule(
            steps=[ScheduleStep(0.0, 10.0), ScheduleStep(10.0, 20.0)]
        )
        assert schedule.setpoint_at(-5.0) == 10.0

    def test_setpoint_at_after_last(self) -> None:
        schedule = SetpointSchedule(
            steps=[ScheduleStep(0.0, 10.0), ScheduleStep(10.0, 20.0)]
        )
        assert schedule.setpoint_at(15.0) == 20.0

    def test_setpoint_at_midpoint(self) -> None:
        schedule = SetpointSchedule(
            steps=[ScheduleStep(0.0, 10.0), ScheduleStep(10.0, 20.0)]
        )
        assert schedule.setpoint_at(5.0) == pytest.approx(15.0)

    def test_setpoint_at_exact_step(self) -> None:
        schedule = SetpointSchedule(
            steps=[ScheduleStep(0.0, 10.0), ScheduleStep(10.0, 20.0)]
        )
        assert schedule.setpoint_at(0.0) == 10.0
        assert schedule.setpoint_at(10.0) == 20.0

    def test_setpoint_at_three_steps(self) -> None:
        schedule = SetpointSchedule(
            steps=[
                ScheduleStep(0.0, 10.0),
                ScheduleStep(10.0, 30.0),
                ScheduleStep(20.0, 20.0),
            ]
        )
        # Midpoint of first segment
        assert schedule.setpoint_at(5.0) == pytest.approx(20.0)
        # Midpoint of second segment
        assert schedule.setpoint_at(15.0) == pytest.approx(25.0)

    def test_setpoint_at_empty_raises(self) -> None:
        schedule = SetpointSchedule(steps=[])
        with pytest.raises(ValueError, match="no steps"):
            schedule.setpoint_at(0.0)

    def test_duration_minutes(self) -> None:
        schedule = SetpointSchedule(
            steps=[ScheduleStep(5.0, 10.0), ScheduleStep(15.0, 20.0)]
        )
        assert schedule.duration_minutes == 10.0

    def test_duration_empty(self) -> None:
        schedule = SetpointSchedule(steps=[])
        assert schedule.duration_minutes == 0.0


class TestScheduleCSV:
    def test_save_and_load(self, tmp_path: Path) -> None:
        path = tmp_path / "schedule.csv"
        original = SetpointSchedule(
            steps=[ScheduleStep(0.0, 10.0), ScheduleStep(10.0, 20.0)]
        )
        original.save_csv(path)

        loaded = SetpointSchedule.load_csv(path)
        assert len(loaded.steps) == 2
        assert loaded.steps[0].elapsed_minutes == pytest.approx(0.0)
        assert loaded.steps[0].temperature == pytest.approx(10.0)
        assert loaded.steps[1].elapsed_minutes == pytest.approx(10.0)
        assert loaded.steps[1].temperature == pytest.approx(20.0)

    def test_load_skips_header(self, tmp_path: Path) -> None:
        path = tmp_path / "schedule.csv"
        path.write_text("time_min,temp_c\n0.0,10.0\n5.0,15.0\n")
        loaded = SetpointSchedule.load_csv(path)
        assert len(loaded.steps) == 2

    def test_load_empty_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "schedule.csv"
        path.write_text("time_min,temp_c\n")
        with pytest.raises(ValueError, match="No valid schedule steps"):
            SetpointSchedule.load_csv(path)

    def test_load_sorts_by_time(self, tmp_path: Path) -> None:
        path = tmp_path / "schedule.csv"
        path.write_text("time,temp\n10,20\n0,10\n5,15\n")
        loaded = SetpointSchedule.load_csv(path)
        assert loaded.steps[0].elapsed_minutes == 0.0
        assert loaded.steps[1].elapsed_minutes == 5.0
        assert loaded.steps[2].elapsed_minutes == 10.0


    def test_load_duplicate_timestamps(self, tmp_path: Path) -> None:
        path = tmp_path / "schedule.csv"
        path.write_text("time,temp\n0,10\n5,15\n5,20\n10,25\n")
        with pytest.raises(ValueError, match="Duplicate elapsed_minutes"):
            SetpointSchedule.load_csv(path)


class TestScheduleRunner:
    def test_tick_applies_setpoint(self) -> None:
        schedule = SetpointSchedule(
            steps=[ScheduleStep(0.0, 10.0), ScheduleStep(10.0, 20.0)]
        )
        callback = MagicMock()
        runner = ScheduleRunner(schedule, callback)
        runner.start()

        # Immediately after start, should apply first setpoint
        result = runner.tick()
        assert result is not None
        callback.assert_called()

    def test_not_running_returns_none(self) -> None:
        schedule = SetpointSchedule(
            steps=[ScheduleStep(0.0, 10.0)]
        )
        callback = MagicMock()
        runner = ScheduleRunner(schedule, callback)
        assert runner.tick() is None

    def test_stop(self) -> None:
        schedule = SetpointSchedule(
            steps=[ScheduleStep(0.0, 10.0), ScheduleStep(10.0, 20.0)]
        )
        callback = MagicMock()
        runner = ScheduleRunner(schedule, callback)
        runner.start()
        assert runner.is_running is True

        runner.stop()
        assert runner.is_finished is True
        assert runner.is_running is False

    def test_finishes_after_last_step(self) -> None:
        schedule = SetpointSchedule(
            steps=[ScheduleStep(0.0, 10.0)]
        )
        callback = MagicMock()
        runner = ScheduleRunner(schedule, callback)

        # Set start time far in the past so elapsed > last step
        runner._start_time = 0.0  # epoch
        runner._finished = False
        result = runner.tick()
        assert result == pytest.approx(10.0)
        assert runner.is_finished is True

    def test_elapsed_minutes_zero_before_start(self) -> None:
        schedule = SetpointSchedule(steps=[ScheduleStep(0.0, 10.0)])
        runner = ScheduleRunner(schedule, MagicMock())
        assert runner.elapsed_minutes == 0.0


class TestFromCsvString:
    def test_parses_csv_string(self) -> None:
        csv_data = "elapsed_minutes,temperature_c\n0,20\n10,30\n"
        schedule = SetpointSchedule.from_csv_string(csv_data)
        assert len(schedule.steps) == 2
        assert schedule.steps[0].temperature == 20.0
        assert schedule.steps[1].temperature == 30.0

    def test_matches_load_csv(self, tmp_path) -> None:
        csv_data = "elapsed_minutes,temperature_c\n0,20\n5,25\n10,30\n"
        path = tmp_path / "schedule.csv"
        path.write_text(csv_data)
        from_file = SetpointSchedule.load_csv(path)
        from_string = SetpointSchedule.from_csv_string(csv_data)
        assert len(from_file.steps) == len(from_string.steps)
        for a, b in zip(from_file.steps, from_string.steps):
            assert a.elapsed_minutes == b.elapsed_minutes
            assert a.temperature == b.temperature

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="No valid schedule steps"):
            SetpointSchedule.from_csv_string("")

    def test_csv_temp_below_min_raises(self) -> None:
        from julabo_control.core import SETPOINT_MIN
        low = SETPOINT_MIN - 10
        csv_data = f"time,temp\n0,{low}\n10,20\n"
        with pytest.raises(ValueError, match="outside the allowed range"):
            SetpointSchedule.from_csv_string(csv_data)

    def test_csv_temp_above_max_raises(self) -> None:
        from julabo_control.core import SETPOINT_MAX
        high = SETPOINT_MAX + 10
        csv_data = f"time,temp\n0,20\n10,{high}\n"
        with pytest.raises(ValueError, match="outside the allowed range"):
            SetpointSchedule.from_csv_string(csv_data)
