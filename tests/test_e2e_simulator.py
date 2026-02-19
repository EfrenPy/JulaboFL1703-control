"""End-to-end tests using the simulator + TCP server in subprocesses.

Unix-only: requires PTY support for the serial simulator.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Requires Unix PTY support")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _send_recv(port: int, message: dict) -> dict:
    """Send a JSON command and read the JSON response."""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(json.dumps(message).encode() + b"\n")
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.strip())


@pytest.fixture(scope="module")
def e2e_server():
    """Start simulator + TCP server as subprocesses."""
    tcp_port = _find_free_port()

    # Start the simulator
    sim_proc = subprocess.Popen(
        [sys.executable, "-m", "julabo_control.simulator", "--noise", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Wait for the simulator to print its PTY path
    device_path = None
    deadline = time.monotonic() + 10
    assert sim_proc.stdout is not None
    while time.monotonic() < deadline:
        line = sim_proc.stdout.readline()
        if not line:
            break
        if "listening on" in line.lower():
            # Expected: "Simulator listening on /dev/pts/X"
            device_path = line.strip().split()[-1]
            break

    if device_path is None:
        sim_proc.kill()
        pytest.skip("Could not get simulator device path")

    # Start the TCP server pointing at the simulator's PTY
    srv_proc = subprocess.Popen(
        [
            sys.executable, "-m", "julabo_control.remote_server",
            device_path,
            "--host", "127.0.0.1",
            "--port", str(tcp_port),
            "--no-watchdog",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # Wait for server to be ready
    deadline = time.monotonic() + 15
    ready = False
    while time.monotonic() < deadline:
        try:
            resp = _send_recv(tcp_port, {"command": "ping"})
            if resp.get("result") == "pong":
                ready = True
                break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)

    if not ready:
        srv_proc.kill()
        sim_proc.kill()
        pytest.skip("Server did not become ready in time")

    yield tcp_port

    srv_proc.kill()
    srv_proc.wait(timeout=5)
    sim_proc.kill()
    sim_proc.wait(timeout=5)


class TestE2E:
    def test_ping(self, e2e_server: int) -> None:
        resp = _send_recv(e2e_server, {"command": "ping"})
        assert resp["status"] == "ok"
        assert resp["result"] == "pong"

    def test_identify_returns_simulator_string(self, e2e_server: int) -> None:
        resp = _send_recv(e2e_server, {"command": "identify"})
        assert resp["status"] == "ok"
        assert "simulator" in resp["result"].lower()

    def test_set_and_get_setpoint(self, e2e_server: int) -> None:
        resp = _send_recv(
            e2e_server, {"command": "set_setpoint", "value": 25.0},
        )
        assert resp["status"] == "ok"
        resp = _send_recv(e2e_server, {"command": "get_setpoint"})
        assert resp["status"] == "ok"
        assert resp["result"] == 25.0

    def test_start_stop(self, e2e_server: int) -> None:
        resp = _send_recv(e2e_server, {"command": "start"})
        assert resp["status"] == "ok"
        resp = _send_recv(e2e_server, {"command": "is_running"})
        assert resp["status"] == "ok"
        assert resp["result"] is True

        resp = _send_recv(e2e_server, {"command": "stop"})
        assert resp["status"] == "ok"
        resp = _send_recv(e2e_server, {"command": "is_running"})
        assert resp["status"] == "ok"
        assert resp["result"] is False

    def test_full_status_all_pipeline(self, e2e_server: int) -> None:
        resp = _send_recv(e2e_server, {"command": "status_all"})
        assert resp["status"] == "ok"
        result = resp["result"]
        assert "temperature" in result
        assert "setpoint" in result
        assert "is_running" in result
        assert "status" in result
