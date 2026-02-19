"""Integration tests using a real TCP server and client."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from julabo_control.remote_client import RemoteChillerClient
from julabo_control.remote_server import PROTOCOL_VERSION, JulaboTCPServer
from julabo_control.simulator import FakeChillerBackend


@pytest.fixture
def mock_chiller() -> MagicMock:
    chiller = MagicMock()
    chiller.identify.return_value = "JULABO FL1703"
    chiller.get_status.return_value = "01 OK"
    chiller.get_temperature.return_value = 21.5
    chiller.get_setpoint.return_value = 20.0
    chiller.is_running.return_value = True
    chiller.set_setpoint.return_value = None
    return chiller


@pytest.fixture
def server(mock_chiller: MagicMock):
    """Start a real JulaboTCPServer on a random port."""
    srv = JulaboTCPServer(("127.0.0.1", 0), mock_chiller)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def client(server: JulaboTCPServer) -> RemoteChillerClient:
    host, port = server.server_address
    return RemoteChillerClient(host, port, timeout=5.0, retries=1)


class TestRoundTrip:
    def test_identify(self, client: RemoteChillerClient) -> None:
        result = client.command("identify")
        assert result == "JULABO FL1703"

    def test_status_all(self, client: RemoteChillerClient) -> None:
        data = client.status_all()
        assert data["status"] == "01 OK"
        assert data["temperature"] == 21.5
        assert data["setpoint"] == 20.0
        assert data["is_running"] is True

    def test_set_setpoint(
        self, client: RemoteChillerClient, mock_chiller: MagicMock
    ) -> None:
        mock_chiller.get_setpoint.return_value = 25.0
        result = client.command("set_setpoint", 25.0)
        assert result == 25.0
        mock_chiller.set_setpoint.assert_called_with(25.0)

    def test_ping(self, client: RemoteChillerClient) -> None:
        result = client.command("ping")
        assert result == "pong"

    def test_protocol_version_in_response(
        self, server: JulaboTCPServer, client: RemoteChillerClient
    ) -> None:
        response = client._send({"command": "ping"})
        assert response["protocol_version"] == PROTOCOL_VERSION


class TestAuthIntegration:
    def test_auth_required_rejects_unauthenticated(
        self, mock_chiller: MagicMock
    ) -> None:
        srv = JulaboTCPServer(
            ("127.0.0.1", 0), mock_chiller, auth_token="secret"
        )
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = srv.server_address
            client = RemoteChillerClient(host, port, timeout=5.0, retries=1)
            with pytest.raises(RuntimeError, match="Authentication failed"):
                client.command("ping")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_auth_accepted(self, mock_chiller: MagicMock) -> None:
        srv = JulaboTCPServer(
            ("127.0.0.1", 0), mock_chiller, auth_token="secret"
        )
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = srv.server_address
            client = RemoteChillerClient(
                host, port, timeout=5.0, retries=1, auth_token="secret"
            )
            result = client.command("ping")
            assert result == "pong"
        finally:
            srv.shutdown()
            srv.server_close()


class TestRateLimitIntegration:
    def test_rate_limit_enforced(self, mock_chiller: MagicMock) -> None:
        srv = JulaboTCPServer(
            ("127.0.0.1", 0), mock_chiller, rate_limit=2
        )
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = srv.server_address
            client = RemoteChillerClient(host, port, timeout=5.0, retries=1)
            # First two should succeed
            assert client.command("ping") == "pong"
            assert client.command("ping") == "pong"
            # Third should be rate-limited
            with pytest.raises(RuntimeError, match="Rate limit"):
                client.command("ping")
        finally:
            srv.shutdown()
            srv.server_close()


# -- Simulator-backed integration tests --


@pytest.fixture
def sim_server():
    """Spin up a real TCP server backed by FakeChillerBackend."""
    backend = FakeChillerBackend(noise=0.0)
    srv = JulaboTCPServer(("127.0.0.1", 0), backend)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    port = srv.server_address[1]
    yield srv, backend, port
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def sim_client(sim_server):
    """Create a RemoteChillerClient connected to the simulator server."""
    _srv, _backend, port = sim_server
    return RemoteChillerClient("127.0.0.1", port, timeout=5.0, retries=1)


class TestSimulatorIntegration:
    def test_identify_roundtrip(self, sim_client: RemoteChillerClient) -> None:
        result = sim_client.command("identify")
        assert "Simulator" in result

    def test_status_all_roundtrip(self, sim_client: RemoteChillerClient) -> None:
        data = sim_client.status_all()
        assert "temperature" in data
        assert "setpoint" in data
        assert "is_running" in data
        assert "status" in data

    def test_set_setpoint_roundtrip(self, sim_client: RemoteChillerClient) -> None:
        result = sim_client.command("set_setpoint", 25.0)
        assert result == 25.0
        data = sim_client.status_all()
        assert data["setpoint"] == 25.0

    def test_start_stop_roundtrip(self, sim_client: RemoteChillerClient) -> None:
        sim_client.command("start")
        data = sim_client.status_all()
        assert data["is_running"] is True
        sim_client.command("stop")
        data = sim_client.status_all()
        assert data["is_running"] is False

    def test_ping_roundtrip(self, sim_client: RemoteChillerClient) -> None:
        result = sim_client.command("ping")
        assert result == "pong"

    def test_schedule_roundtrip(self, sim_client: RemoteChillerClient) -> None:
        csv = "elapsed_minutes,temperature_c\n0,20\n10,30\n"
        result = sim_client.load_schedule(csv)
        assert result["steps"] == 2
        status = sim_client.schedule_status()
        assert status["running"] is True
        stop_result = sim_client.stop_schedule()
        assert stop_result == "stopped"

    def test_auth_rejected_simulator(self) -> None:
        backend = FakeChillerBackend(noise=0.0)
        srv = JulaboTCPServer(
            ("127.0.0.1", 0), backend, auth_token="secret"
        )
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        port = srv.server_address[1]
        try:
            no_auth = RemoteChillerClient(
                "127.0.0.1", port, timeout=2.0, retries=1
            )
            with pytest.raises(RuntimeError, match="Authentication failed"):
                no_auth.command("ping")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_read_only_rejects_writes(self) -> None:
        backend = FakeChillerBackend(noise=0.0)
        srv = JulaboTCPServer(
            ("127.0.0.1", 0), backend, read_only=True
        )
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        port = srv.server_address[1]
        try:
            c = RemoteChillerClient("127.0.0.1", port, timeout=2.0, retries=1)
            result = c.command("ping")
            assert result == "pong"
            with pytest.raises(RuntimeError, match="read-only"):
                c.command("set_setpoint", 25.0)
        finally:
            srv.shutdown()
            srv.server_close()


class TestMultiChillerIntegration:
    def test_two_simulators(self) -> None:
        backend1 = FakeChillerBackend(noise=0.0, initial_temp=20.0)
        backend2 = FakeChillerBackend(noise=0.0, initial_temp=30.0)
        srv = JulaboTCPServer(("127.0.0.1", 0), backend1)
        srv.add_chiller("chiller2", backend2)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        port = srv.server_address[1]
        try:
            c = RemoteChillerClient("127.0.0.1", port, timeout=2.0, retries=1)
            # Default chiller
            d1 = c._send({"command": "temperature"})
            assert abs(d1["result"] - 20.0) < 1.0
            # Second chiller
            d2 = c._send({"command": "temperature", "chiller_id": "chiller2"})
            assert abs(d2["result"] - 30.0) < 1.0
        finally:
            srv.shutdown()
            srv.server_close()
