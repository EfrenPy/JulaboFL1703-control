"""Setpoint schedule support for automated temperature ramps."""

from __future__ import annotations

import csv
import io
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Callable

from .core import SETPOINT_MAX, SETPOINT_MIN

LOGGER = logging.getLogger(__name__)


@dataclass
class ScheduleStep:
    """A single step in a setpoint schedule.

    Parameters
    ----------
    elapsed_minutes:
        Minutes from the start of the schedule at which this setpoint applies.
    temperature:
        Target setpoint in degrees Celsius.
    """

    elapsed_minutes: float
    temperature: float


@dataclass
class SetpointSchedule:
    """An ordered list of setpoint steps with linear interpolation.

    Steps must be ordered by ``elapsed_minutes`` (ascending).  Between steps
    the setpoint is linearly interpolated.  Before the first step or after the
    last step the nearest step's temperature is used (hold).
    """

    steps: list[ScheduleStep] = field(default_factory=list)

    # -- persistence --

    @classmethod
    def _parse_csv(cls, fh: IO[str], source: str = "<csv>") -> SetpointSchedule:
        """Parse a schedule from a file-like object with two CSV columns."""
        steps: list[ScheduleStep] = []
        reader = csv.reader(fh)
        for i, row in enumerate(reader):
            if len(row) < 2:
                continue
            try:
                minutes = float(row[0])
                temp = float(row[1])
            except ValueError:
                if i == 0:
                    continue  # skip header
                raise
            if not (SETPOINT_MIN <= temp <= SETPOINT_MAX):
                raise ValueError(
                    f"Temperature {temp} in schedule is outside the allowed range "
                    f"[{SETPOINT_MIN}, {SETPOINT_MAX}]"
                )
            steps.append(ScheduleStep(elapsed_minutes=minutes, temperature=temp))
        if not steps:
            raise ValueError(f"No valid schedule steps found in {source}")
        steps.sort(key=lambda s: s.elapsed_minutes)
        duplicates = [
            steps[i].elapsed_minutes
            for i in range(1, len(steps))
            if steps[i].elapsed_minutes == steps[i - 1].elapsed_minutes
        ]
        if duplicates:
            unique_dups = sorted(set(duplicates))
            raise ValueError(
                f"Duplicate elapsed_minutes values in schedule: {unique_dups}"
            )
        return cls(steps=steps)

    @classmethod
    def load_csv(cls, path: str | Path) -> SetpointSchedule:
        """Load a schedule from a two-column CSV (elapsed_minutes, temperature_c).

        The first row is treated as a header if it cannot be parsed as floats.
        """
        with open(path, newline="") as fh:
            return cls._parse_csv(fh, source=str(path))

    @classmethod
    def from_csv_string(cls, csv_string: str) -> SetpointSchedule:
        """Parse a schedule from a CSV string."""
        return cls._parse_csv(io.StringIO(csv_string), source="<string>")

    def save_csv(self, path: str | Path) -> None:
        """Write the schedule to a two-column CSV file."""
        with open(path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["elapsed_minutes", "temperature_c"])
            for step in self.steps:
                writer.writerow([f"{step.elapsed_minutes:.2f}", f"{step.temperature:.2f}"])

    # -- interpolation --

    def setpoint_at(self, elapsed_minutes: float) -> float:
        """Return the interpolated setpoint for the given elapsed time.

        Before the first step, returns the first step's temperature (hold).
        After the last step, returns the last step's temperature (hold).
        Between two steps, linearly interpolates.
        """
        if not self.steps:
            raise ValueError("Schedule has no steps")

        if elapsed_minutes <= self.steps[0].elapsed_minutes:
            return self.steps[0].temperature

        if elapsed_minutes >= self.steps[-1].elapsed_minutes:
            return self.steps[-1].temperature

        for i in range(len(self.steps) - 1):
            a, b = self.steps[i], self.steps[i + 1]
            if a.elapsed_minutes <= elapsed_minutes <= b.elapsed_minutes:
                span = b.elapsed_minutes - a.elapsed_minutes
                if span == 0:
                    return b.temperature
                fraction = (elapsed_minutes - a.elapsed_minutes) / span
                return a.temperature + fraction * (b.temperature - a.temperature)

        return self.steps[-1].temperature  # pragma: no cover

    @property
    def duration_minutes(self) -> float:
        """Total duration of the schedule in minutes."""
        if not self.steps:
            return 0.0
        return self.steps[-1].elapsed_minutes - self.steps[0].elapsed_minutes


class ScheduleRunner:
    """Executes a :class:`SetpointSchedule` against a callback.

    The runner is meant to be polled periodically (e.g. from a Tk ``after``
    callback).  Each call to :meth:`tick` computes the interpolated setpoint
    and invokes the supplied ``apply_setpoint`` callback when the value changes
    by more than ``tolerance``.
    """

    def __init__(
        self,
        schedule: SetpointSchedule,
        apply_setpoint: Callable[[float], None],
        *,
        tolerance: float = 0.05,
    ) -> None:
        self.schedule = schedule
        self._apply = apply_setpoint
        self._tolerance = tolerance
        self._start_time: float | None = None
        self._last_setpoint: float | None = None
        self._finished = False

    @property
    def is_running(self) -> bool:
        return self._start_time is not None and not self._finished

    @property
    def is_finished(self) -> bool:
        return self._finished

    @property
    def elapsed_minutes(self) -> float:
        if self._start_time is None:
            return 0.0
        return (time.time() - self._start_time) / 60.0

    def start(self) -> None:
        """Begin executing the schedule from the current moment."""
        self._start_time = time.time()
        self._finished = False
        self._last_setpoint = None
        LOGGER.info("Schedule started (%d steps)", len(self.schedule.steps))

    def stop(self) -> None:
        """Stop the schedule execution."""
        self._finished = True
        LOGGER.info("Schedule stopped")

    def tick(self) -> float | None:
        """Compute the current setpoint and apply if changed.

        Returns the interpolated setpoint, or ``None`` if the schedule is not
        running.
        """
        if self._start_time is None or self._finished:
            return None

        elapsed = self.elapsed_minutes
        target = self.schedule.setpoint_at(elapsed)

        if elapsed >= self.schedule.steps[-1].elapsed_minutes:
            # Apply the final setpoint and mark as finished
            if self._last_setpoint is None or abs(target - self._last_setpoint) > self._tolerance:
                self._apply(target)
                self._last_setpoint = target
            self._finished = True
            LOGGER.info("Schedule finished")
            return target

        if self._last_setpoint is None or abs(target - self._last_setpoint) > self._tolerance:
            self._apply(target)
            self._last_setpoint = target
            LOGGER.debug("Schedule setpoint: %.2f °C at %.1f min", target, elapsed)

        return target
