"""SQLite persistence for temperature readings."""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any


class TemperatureDB:
    """Thread-safe SQLite store for temperature readings."""

    _SCHEMA = """\
    CREATE TABLE IF NOT EXISTS temperature_readings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        chiller_id TEXT NOT NULL DEFAULT 'default',
        temperature REAL NOT NULL,
        setpoint REAL NOT NULL,
        is_running INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_readings_ts ON temperature_readings(timestamp);
    """

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.executescript(self._SCHEMA)
        self._path = path

    def record(
        self,
        temperature: float,
        setpoint: float,
        chiller_id: str = "default",
        is_running: bool = False,
    ) -> None:
        """Insert a temperature reading."""
        ts = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO temperature_readings "
                "(timestamp, chiller_id, temperature, setpoint, is_running) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, chiller_id, temperature, setpoint, int(is_running)),
            )
            self._conn.commit()

    def query_recent(
        self, minutes: int = 60, chiller_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return recent readings as a list of dicts."""
        cutoff = time.time() - minutes * 60
        with self._lock:
            if chiller_id is not None:
                cursor = self._conn.execute(
                    "SELECT * FROM temperature_readings "
                    "WHERE timestamp >= ? AND chiller_id = ? ORDER BY timestamp",
                    (cutoff, chiller_id),
                )
            else:
                cursor = self._conn.execute(
                    "SELECT * FROM temperature_readings "
                    "WHERE timestamp >= ? ORDER BY timestamp",
                    (cutoff,),
                )
            return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()
