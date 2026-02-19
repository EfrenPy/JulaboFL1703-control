"""Tests for WebSocket support in julabo_control.web."""

from __future__ import annotations

import json
import socket
import threading
import time
from unittest.mock import MagicMock

import pytest

ws_sync = pytest.importorskip("websockets.sync.client")

from julabo_control.web import JulaboWebServer  # noqa: E402


@pytest.fixture
def ws_web_server():
    """Start a JulaboWebServer with WebSocket enabled."""
    # Find a free port for WebSocket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    ws_port = sock.getsockname()[1]
    sock.close()

    client = MagicMock()
    client.status_all.return_value = {
        "status": "01 OK",
        "temperature": 21.5,
        "setpoint": 20.0,
        "is_running": True,
    }
    server = JulaboWebServer(
        ("127.0.0.1", 0), client, sse_interval=0.1, ws_port=ws_port,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    time.sleep(0.3)  # Give WS server time to start

    yield server, client, ws_port
    server.shutdown()
    thread.join(timeout=5)


class TestWebSocket:
    def test_ws_connect_receives_status_push(self, ws_web_server) -> None:
        _, client, ws_port = ws_web_server
        with ws_sync.connect(f"ws://127.0.0.1:{ws_port}", open_timeout=5) as ws:
            # First message is "connected"
            msg = json.loads(ws.recv(timeout=5))
            assert msg["type"] == "connected"
            # Second message should be a status push
            msg = json.loads(ws.recv(timeout=5))
            assert msg["type"] == "status"
            assert msg["temperature"] == 21.5

    def test_ws_send_set_setpoint(self, ws_web_server) -> None:
        _, client, ws_port = ws_web_server
        with ws_sync.connect(f"ws://127.0.0.1:{ws_port}", open_timeout=5) as ws:
            # Consume connection message
            ws.recv(timeout=5)
            # Send setpoint command
            ws.send(json.dumps({"command": "set_setpoint", "value": 25.0}))
            msg = json.loads(ws.recv(timeout=5))
            assert msg["type"] == "ack"
            assert msg["command"] == "set_setpoint"
            client.command.assert_called_with("set_setpoint", 25.0)

    def test_ws_send_start(self, ws_web_server) -> None:
        _, client, ws_port = ws_web_server
        with ws_sync.connect(f"ws://127.0.0.1:{ws_port}", open_timeout=5) as ws:
            ws.recv(timeout=5)
            ws.send(json.dumps({"command": "start"}))
            msg = json.loads(ws.recv(timeout=5))
            assert msg["type"] == "ack"
            assert msg["command"] == "start"
            client.command.assert_called_with("start")

    def test_ws_send_stop(self, ws_web_server) -> None:
        _, client, ws_port = ws_web_server
        with ws_sync.connect(f"ws://127.0.0.1:{ws_port}", open_timeout=5) as ws:
            ws.recv(timeout=5)
            ws.send(json.dumps({"command": "stop"}))
            msg = json.loads(ws.recv(timeout=5))
            assert msg["type"] == "ack"
            assert msg["command"] == "stop"
            client.command.assert_called_with("stop")

    def test_ws_unknown_command(self, ws_web_server) -> None:
        _, client, ws_port = ws_web_server
        with ws_sync.connect(f"ws://127.0.0.1:{ws_port}", open_timeout=5) as ws:
            ws.recv(timeout=5)
            ws.send(json.dumps({"command": "bogus"}))
            msg = json.loads(ws.recv(timeout=5))
            assert msg["type"] == "error"
            assert "Unknown command" in msg["error"]

    def test_ws_set_setpoint_missing_value(self, ws_web_server) -> None:
        _, client, ws_port = ws_web_server
        with ws_sync.connect(f"ws://127.0.0.1:{ws_port}", open_timeout=5) as ws:
            ws.recv(timeout=5)
            ws.send(json.dumps({"command": "set_setpoint"}))
            msg = json.loads(ws.recv(timeout=5))
            assert msg["type"] == "error"

    def test_ws_status_all_exception(self, ws_web_server) -> None:
        _, client, ws_port = ws_web_server
        client.status_all.side_effect = RuntimeError("connection lost")
        with ws_sync.connect(f"ws://127.0.0.1:{ws_port}", open_timeout=5) as ws:
            ws.recv(timeout=5)  # connected message
            msg = json.loads(ws.recv(timeout=5))  # should be error from status push
            assert msg["type"] == "error"
            assert "connection lost" in msg["error"]

    def test_ws_send_invalid_json_returns_error(self, ws_web_server) -> None:
        _, client, ws_port = ws_web_server
        with ws_sync.connect(f"ws://127.0.0.1:{ws_port}", open_timeout=5) as ws:
            # Consume connection message
            ws.recv(timeout=5)
            ws.send("not valid json {{{")
            msg = json.loads(ws.recv(timeout=5))
            assert msg["type"] == "error"
