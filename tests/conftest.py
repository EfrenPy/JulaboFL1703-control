"""Shared test fixtures for Julabo chiller tests."""

from __future__ import annotations

import pytest

from julabo_control.core import JulaboChiller, SerialSettings


class MockSerial:
    """Fake serial port that records writes and queues responses."""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self._responses: list[str] = []
        self.closed: bool = False

    def queue(self, *responses: str) -> None:
        """Queue one or more response lines."""
        self._responses.extend(responses)

    def write(self, data: bytes) -> int:
        self.written.append(data)
        return len(data)

    def readline(self) -> bytes:
        if not self._responses:
            return b""
        return (self._responses.pop(0) + "\r\n").encode("ascii")

    def close(self) -> None:
        self.closed = True

    @property
    def last_command(self) -> str | None:
        """Return the last written command string (without CRLF)."""
        if not self.written:
            return None
        return self.written[-1].decode("ascii").strip()


@pytest.fixture
def mock_serial() -> MockSerial:
    return MockSerial()


@pytest.fixture
def chiller(mock_serial: MockSerial) -> JulaboChiller:
    """Return a JulaboChiller wired to a MockSerial (no real connection)."""
    settings = SerialSettings(port="/dev/null")
    obj = JulaboChiller(settings)
    obj._serial = mock_serial  # bypass connect()
    return obj
