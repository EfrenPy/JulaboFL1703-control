"""Tests for julabo_control.logging_utils."""

from __future__ import annotations

import json
import logging

from julabo_control.logging_utils import JsonFormatter


class TestJsonFormatter:
    def test_basic_format(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        line = formatter.format(record)
        data = json.loads(line)
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert data["message"] == "hello world"
        assert "timestamp" in data

    def test_includes_timestamp_iso(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.DEBUG, pathname="", lineno=0,
            msg="debug msg", args=(), exc_info=None,
        )
        line = formatter.format(record)
        data = json.loads(line)
        assert "T" in data["timestamp"]  # ISO format

    def test_handles_extra_fields(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="cmd", args=(), exc_info=None,
        )
        record.client_ip = "10.0.0.1"  # type: ignore[attr-defined]
        record.command = "get_setpoint"  # type: ignore[attr-defined]
        line = formatter.format(record)
        data = json.loads(line)
        assert data["client_ip"] == "10.0.0.1"
        assert data["command"] == "get_setpoint"

    def test_exception_included(self) -> None:
        formatter = JsonFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="", lineno=0,
                msg="fail", args=(), exc_info=sys.exc_info(),
            )
        line = formatter.format(record)
        data = json.loads(line)
        assert "exception" in data
        assert "ValueError" in data["exception"]

    def test_valid_json_output(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test.sub", level=logging.WARNING, pathname="", lineno=0,
            msg="message with \"quotes\" and \nnewlines", args=(), exc_info=None,
        )
        line = formatter.format(record)
        data = json.loads(line)  # Should not raise
        assert data["message"] == "message with \"quotes\" and \nnewlines"
