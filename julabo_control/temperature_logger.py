"""Append-only CSV logger for temperature data."""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

LOGGER = logging.getLogger(__name__)


class TemperatureFileLogger:
    """Logs temperature readings to a CSV file.

    Each call to :meth:`record` appends a row with the UTC timestamp, elapsed
    minutes since the first reading, the process temperature, and the current
    setpoint.

    The file handle is kept open between writes and flushed after each row for
    durability.  Use :meth:`close` or the context-manager protocol to release
    the handle.
    """

    _HEADER = ["timestamp_utc", "elapsed_minutes", "temperature_c", "setpoint_c"]

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._start_time: float | None = None
        self._fh: IO[str] | None = None
        self._writer: Any = None

    @property
    def path(self) -> Path:
        return self._path

    def _ensure_open(self) -> Any:
        """Open the file (if needed) and return the CSV writer."""
        if self._fh is None or self._fh.closed:
            write_header = not self._path.exists() or os.path.getsize(self._path) == 0
            self._fh = open(self._path, "a", newline="")
            self._writer = csv.writer(self._fh)
            if write_header:
                self._writer.writerow(self._HEADER)
                self._fh.flush()
        return self._writer

    def record(
        self,
        temperature: float,
        setpoint: float,
        *,
        timestamp: float | None = None,
    ) -> None:
        """Append a single temperature row to the log file."""
        import time as _time

        if timestamp is None:
            timestamp = _time.time()

        if self._start_time is None:
            self._start_time = timestamp

        elapsed = (timestamp - self._start_time) / 60.0
        utc_str = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()

        writer = self._ensure_open()
        writer.writerow([utc_str, f"{elapsed:.2f}", f"{temperature:.2f}", f"{setpoint:.2f}"])
        if self._fh is None:
            raise RuntimeError("File handle not open for flushing")
        self._fh.flush()

    def close(self) -> None:
        """Close the file handle and reset internal state."""
        if self._fh is not None and not self._fh.closed:
            self._fh.close()
        self._fh = None
        self._writer = None
        self._start_time = None

    def __enter__(self) -> TemperatureFileLogger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
