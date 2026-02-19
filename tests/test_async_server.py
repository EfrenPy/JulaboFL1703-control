"""Tests for julabo_control.async_server."""

from __future__ import annotations

import asyncio
import json

import pytest

from julabo_control.async_server import AsyncJulaboServer
from julabo_control.simulator import FakeChillerBackend


@pytest.fixture
async def async_server():
    """Start an AsyncJulaboServer on a random port with a fake backend."""
    chiller = FakeChillerBackend(initial_temp=20.0, noise=0.0)
    server = AsyncJulaboServer(
        chiller, "127.0.0.1", 0, auth_token="secret", rate_limit=0,
    )
    await server.start()
    yield server, chiller
    await server.stop()


async def _send_recv(port: int, message: dict) -> dict:
    """Helper: connect, send JSON, read JSON response, close."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(json.dumps(message).encode() + b"\n")
    await writer.drain()
    raw = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=5)
    writer.close()
    await writer.wait_closed()
    return json.loads(raw)


class TestAsyncServer:
    async def test_async_ping_roundtrip(self, async_server) -> None:
        server, _ = async_server
        resp = await _send_recv(
            server.port, {"command": "ping", "token": "secret"},
        )
        assert resp["status"] == "ok"
        assert resp["result"] == "pong"

    async def test_async_status_all(self, async_server) -> None:
        server, _ = async_server
        resp = await _send_recv(
            server.port, {"command": "status_all", "token": "secret"},
        )
        assert resp["status"] == "ok"
        result = resp["result"]
        assert "temperature" in result
        assert "setpoint" in result
        assert "is_running" in result
        assert "status" in result

    async def test_async_set_setpoint(self, async_server) -> None:
        server, chiller = async_server
        resp = await _send_recv(
            server.port,
            {"command": "set_setpoint", "value": 25.0, "token": "secret"},
        )
        assert resp["status"] == "ok"
        assert resp["result"] == 25.0
        assert chiller.get_setpoint() == 25.0

    async def test_async_auth_rejected(self, async_server) -> None:
        server, _ = async_server
        resp = await _send_recv(
            server.port, {"command": "ping", "token": "wrong"},
        )
        assert resp["status"] == "error"
        assert "authentication" in resp["error"].lower()

    async def test_async_rate_limit(self) -> None:
        chiller = FakeChillerBackend(noise=0.0)
        server = AsyncJulaboServer(
            chiller, "127.0.0.1", 0, rate_limit=2,
        )
        await server.start()
        try:
            # First two should pass
            for _ in range(2):
                resp = await _send_recv(
                    server.port, {"command": "ping"},
                )
                assert resp["status"] == "ok"
            # Third should be rate-limited
            resp = await _send_recv(server.port, {"command": "ping"})
            assert resp["status"] == "error"
            assert "rate limit" in resp["error"].lower()
        finally:
            await server.stop()

    async def test_async_concurrent_clients(self, async_server) -> None:
        server, _ = async_server

        async def ping() -> dict:
            return await _send_recv(
                server.port, {"command": "ping", "token": "secret"},
            )

        results = await asyncio.gather(*[ping() for _ in range(5)])
        for resp in results:
            assert resp["status"] == "ok"
            assert resp["result"] == "pong"

    async def test_async_unknown_command_returns_error(self, async_server) -> None:
        server, _ = async_server
        resp = await _send_recv(
            server.port, {"command": "nonexistent", "token": "secret"},
        )
        assert resp["status"] == "error"
        assert "unsupported" in resp["error"].lower()


class TestAsyncServerDispatch:
    """Test all core command branches through the async server."""

    async def test_async_identify(self, async_server) -> None:
        server, chiller = async_server
        resp = await _send_recv(
            server.port, {"command": "identify", "token": "secret"},
        )
        assert resp["status"] == "ok"
        assert "Simulator" in resp["result"]

    async def test_async_status(self, async_server) -> None:
        server, _ = async_server
        resp = await _send_recv(
            server.port, {"command": "status", "token": "secret"},
        )
        assert resp["status"] == "ok"
        assert isinstance(resp["result"], str)

    async def test_async_get_setpoint(self, async_server) -> None:
        server, _ = async_server
        resp = await _send_recv(
            server.port, {"command": "get_setpoint", "token": "secret"},
        )
        assert resp["status"] == "ok"
        assert isinstance(resp["result"], (int, float))

    async def test_async_temperature(self, async_server) -> None:
        server, _ = async_server
        resp = await _send_recv(
            server.port, {"command": "temperature", "token": "secret"},
        )
        assert resp["status"] == "ok"
        assert isinstance(resp["result"], (int, float))

    async def test_async_is_running(self, async_server) -> None:
        server, _ = async_server
        resp = await _send_recv(
            server.port, {"command": "is_running", "token": "secret"},
        )
        assert resp["status"] == "ok"
        assert resp["result"] is False

    async def test_async_start(self, async_server) -> None:
        server, chiller = async_server
        resp = await _send_recv(
            server.port, {"command": "start", "token": "secret"},
        )
        assert resp["status"] == "ok"
        assert chiller.is_running() is True

    async def test_async_stop(self, async_server) -> None:
        server, chiller = async_server
        chiller.start()
        resp = await _send_recv(
            server.port, {"command": "stop", "token": "secret"},
        )
        assert resp["status"] == "ok"
        assert chiller.is_running() is False

    async def test_async_set_running(self, async_server) -> None:
        server, chiller = async_server
        resp = await _send_recv(
            server.port, {"command": "set_running", "value": True, "token": "secret"},
        )
        assert resp["status"] == "ok"
        assert chiller.is_running() is True

    async def test_async_set_setpoint_missing_value(self, async_server) -> None:
        server, _ = async_server
        resp = await _send_recv(
            server.port, {"command": "set_setpoint", "token": "secret"},
        )
        assert resp["status"] == "error"
        assert "value" in resp["error"].lower()

    async def test_async_set_running_missing_value(self, async_server) -> None:
        server, _ = async_server
        resp = await _send_recv(
            server.port, {"command": "set_running", "token": "secret"},
        )
        assert resp["status"] == "error"
        assert "value" in resp["error"].lower()

    async def test_async_no_command_key(self, async_server) -> None:
        server, _ = async_server
        resp = await _send_recv(
            server.port, {"token": "secret"},
        )
        assert resp["status"] == "error"
        assert "command" in resp["error"].lower()


class TestAsyncServerLifecycle:
    async def test_async_stop_before_start(self) -> None:
        chiller = FakeChillerBackend(noise=0.0)
        server = AsyncJulaboServer(chiller, "127.0.0.1", 0)
        # Calling stop on an unstarted server should not raise
        await server.stop()

    async def test_async_stop_after_start(self) -> None:
        chiller = FakeChillerBackend(noise=0.0)
        server = AsyncJulaboServer(chiller, "127.0.0.1", 0)
        await server.start()
        port = server.port
        await server.stop()
        # Port should no longer be listening
        with pytest.raises(OSError):
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()

    async def test_async_read_only_blocks_writes(self) -> None:
        chiller = FakeChillerBackend(noise=0.0)
        server = AsyncJulaboServer(chiller, "127.0.0.1", 0, read_only=True)
        await server.start()
        try:
            resp = await _send_recv(
                server.port, {"command": "set_setpoint", "value": 30.0},
            )
            assert resp["status"] == "error"
            assert "read-only" in resp["error"]
        finally:
            await server.stop()

    async def test_async_oversized_message_rejected(self) -> None:
        chiller = FakeChillerBackend(noise=0.0)
        server = AsyncJulaboServer(chiller, "127.0.0.1", 0)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            # Send a message larger than MAX_MESSAGE_SIZE (1 MB)
            big = b"x" * (1_048_576 + 100) + b"\n"
            try:
                writer.write(big)
                await writer.drain()
            except (BrokenPipeError, ConnectionError):
                pass
            try:
                raw = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=5)
                resp = json.loads(raw)
                assert resp["status"] == "error"
                assert "too large" in resp["error"].lower()
            except (asyncio.IncompleteReadError, asyncio.TimeoutError):
                # Server closed the connection — acceptable behavior
                pass
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        finally:
            await server.stop()

    async def test_async_malformed_json_returns_error(self) -> None:
        chiller = FakeChillerBackend(noise=0.0)
        server = AsyncJulaboServer(chiller, "127.0.0.1", 0)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            writer.write(b"not valid json\n")
            await writer.drain()
            raw = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=5)
            resp = json.loads(raw)
            assert resp["status"] == "error"
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

    async def test_async_connection_reset_handled(self) -> None:
        chiller = FakeChillerBackend(noise=0.0)
        server = AsyncJulaboServer(chiller, "127.0.0.1", 0)
        await server.start()
        try:
            # Connect and immediately close — should not crash server
            _, writer = await asyncio.open_connection("127.0.0.1", server.port)
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.1)
            # Server should still be alive and accept new connections
            resp = await _send_recv(server.port, {"command": "ping"})
            assert resp["status"] == "ok"
        finally:
            await server.stop()
