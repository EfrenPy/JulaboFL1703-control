"""Tests for julabo_control.db."""

from __future__ import annotations

import threading
import time

from julabo_control.db import TemperatureDB


class TestTemperatureDB:
    def test_record_and_query(self) -> None:
        db = TemperatureDB(":memory:")
        db.record(21.5, 20.0, is_running=True)
        rows = db.query_recent(60)
        assert len(rows) == 1
        assert rows[0]["temperature"] == 21.5
        assert rows[0]["setpoint"] == 20.0
        assert rows[0]["is_running"] == 1
        db.close()

    def test_query_filters_by_time(self) -> None:
        db = TemperatureDB(":memory:")
        # Insert a reading "in the past"
        with db._lock:
            old_ts = time.time() - 7200  # 2 hours ago
            db._conn.execute(
                "INSERT INTO temperature_readings "
                "(timestamp, chiller_id, temperature, setpoint, is_running) "
                "VALUES (?, ?, ?, ?, ?)",
                (old_ts, "default", 18.0, 20.0, 0),
            )
            db._conn.commit()
        db.record(21.5, 20.0)
        rows = db.query_recent(60)
        assert len(rows) == 1
        assert rows[0]["temperature"] == 21.5
        db.close()

    def test_query_filters_by_chiller_id(self) -> None:
        db = TemperatureDB(":memory:")
        db.record(21.5, 20.0, chiller_id="ch1")
        db.record(30.0, 25.0, chiller_id="ch2")
        rows = db.query_recent(60, chiller_id="ch1")
        assert len(rows) == 1
        assert rows[0]["chiller_id"] == "ch1"
        db.close()

    def test_thread_safe_writes(self) -> None:
        db = TemperatureDB(":memory:")
        errors: list[Exception] = []

        def writer(offset: float) -> None:
            try:
                for i in range(20):
                    db.record(20.0 + offset + i * 0.1, 20.0)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        rows = db.query_recent(60)
        assert len(rows) == 80
        db.close()

    def test_close_and_reopen(self, tmp_path) -> None:
        path = str(tmp_path / "test.db")
        db = TemperatureDB(path)
        db.record(21.5, 20.0)
        db.close()

        db2 = TemperatureDB(path)
        rows = db2.query_recent(60)
        assert len(rows) == 1
        assert rows[0]["temperature"] == 21.5
        db2.close()

    def test_in_memory_db(self) -> None:
        db = TemperatureDB()
        db.record(22.0, 20.0)
        rows = db.query_recent(60)
        assert len(rows) == 1
        db.close()
