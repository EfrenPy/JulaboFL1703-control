"""Tests for Prometheus metrics support."""

from __future__ import annotations

import urllib.request

from julabo_control.remote_server import _MetricsHTTPServer, _MetricsState


class TestMetricsState:
    def test_record_command(self) -> None:
        m = _MetricsState()
        m.record_command("temperature", 0.01)
        assert m.commands_total["temperature"] == 1
        assert len(m.command_latencies) == 1

    def test_cache_status(self) -> None:
        m = _MetricsState()
        m.cache_status({"temperature": 21.5, "setpoint": 20.0, "is_running": True})
        assert m.last_temperature == 21.5
        assert m.last_setpoint == 20.0
        assert m.last_running is True

    def test_render_empty(self) -> None:
        m = _MetricsState()
        text = m.render_prometheus()
        # Empty state should produce minimal output (no gauges, no counters)
        assert text.strip() == ""

    def test_render_with_data(self) -> None:
        m = _MetricsState()
        m.record_command("temperature", 0.005)
        m.record_command("temperature", 0.015)
        m.cache_status({"temperature": 21.5, "setpoint": 20.0, "is_running": False})
        text = m.render_prometheus()
        assert "julabo_temperature_celsius 21.5" in text
        assert "julabo_setpoint_celsius 20.0" in text
        assert "julabo_pump_running 0" in text
        assert 'julabo_commands_total{command="temperature"} 2' in text
        assert "julabo_command_latency_seconds_bucket" in text
        assert "julabo_command_latency_seconds_count 2" in text

    def test_latency_capped(self) -> None:
        m = _MetricsState()
        for _i in range(1500):
            m.record_command("ping", 0.001)
        assert len(m.command_latencies) <= 1000


class TestMetricsHTTPServer:
    def test_get_metrics(self) -> None:
        m = _MetricsState()
        m.record_command("ping", 0.002)
        m.cache_status({"temperature": 22.0, "setpoint": 20.0, "is_running": True})

        server = _MetricsHTTPServer(("127.0.0.1", 0), m)
        server.start()
        try:
            port = server._server.server_address[1]
            url = f"http://127.0.0.1:{port}/metrics"
            with urllib.request.urlopen(url, timeout=5) as resp:
                assert resp.status == 200
                body = resp.read().decode()
                assert "julabo_temperature_celsius" in body
                assert "julabo_pump_running 1" in body
        finally:
            server.stop()

    def test_404_on_wrong_path(self) -> None:
        m = _MetricsState()
        server = _MetricsHTTPServer(("127.0.0.1", 0), m)
        server.start()
        try:
            port = server._server.server_address[1]
            url = f"http://127.0.0.1:{port}/wrong"
            try:
                urllib.request.urlopen(url, timeout=5)
                raise AssertionError("Should have raised")
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
        finally:
            server.stop()

    def test_empty_data_ok(self) -> None:
        m = _MetricsState()
        server = _MetricsHTTPServer(("127.0.0.1", 0), m)
        server.start()
        try:
            port = server._server.server_address[1]
            url = f"http://127.0.0.1:{port}/metrics"
            with urllib.request.urlopen(url, timeout=5) as resp:
                assert resp.status == 200
        finally:
            server.stop()
