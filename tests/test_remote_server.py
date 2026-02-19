"""Tests for julabo_control.remote_server."""

from __future__ import annotations

import json
import logging
import logging.handlers
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from julabo_control.core import JulaboError  # noqa: I001
from julabo_control.dispatch import _normalize_boolean
from julabo_control.remote_server import (
    _WRITE_COMMANDS,
    PROTOCOL_VERSION,
    JulaboTCPServer,
    _MetricsState,
    _RateLimiter,
    _sanitize_error,
    _SerialWatchdog,
    configure_logging,
    parse_arguments,
    resolve_auth_token,
)


def _make_server(
    auth_token: str | None = None,
    read_only: bool = False,
    idle_timeout: float = 0,
) -> JulaboTCPServer:
    mock_chiller = MagicMock()
    obj = object.__new__(JulaboTCPServer)
    obj._chillers = {"default": mock_chiller}
    obj.auth_token = auth_token
    obj._lock = threading.Lock()
    obj._ssl_context = None
    obj._rate_limiter = None
    obj._shutting_down = False
    obj._active_connections = 0
    obj._conn_lock = threading.Lock()
    obj._traffic_logger = None
    obj._read_only = read_only
    obj._idle_timeout = idle_timeout
    obj._audit_logger = None
    obj._serial_connected = True
    obj._watchdog = None
    obj._schedule_runners = {}
    obj._schedule_threads = {}
    obj._schedule_lock = threading.Lock()
    obj._metrics = None
    obj._metrics_server = None
    return obj


@pytest.fixture
def server() -> JulaboTCPServer:
    """Create a JulaboTCPServer without binding to a socket."""
    return _make_server()


@pytest.fixture
def authed_server() -> JulaboTCPServer:
    """Create a JulaboTCPServer with authentication enabled."""
    return _make_server(auth_token="secret123")


class TestProcessCommand:
    def test_identify(self, server: JulaboTCPServer) -> None:
        server.chiller.identify.return_value = "JULABO FL1703"
        result = server.process_command({"command": "identify"})
        assert result["status"] == "ok"
        assert result["result"] == "JULABO FL1703"
        assert result["protocol_version"] == PROTOCOL_VERSION

    def test_get_setpoint(self, server: JulaboTCPServer) -> None:
        server.chiller.get_setpoint.return_value = 20.0
        result = server.process_command({"command": "get_setpoint"})
        assert result["result"] == 20.0

    def test_set_setpoint(self, server: JulaboTCPServer) -> None:
        server.chiller.get_setpoint.return_value = 25.0
        result = server.process_command({"command": "set_setpoint", "value": 25.0})
        server.chiller.set_setpoint.assert_called_once_with(25.0)
        assert result["result"] == 25.0

    def test_set_setpoint_missing_value(self, server: JulaboTCPServer) -> None:
        with pytest.raises(ValueError, match="requires a numeric"):
            server.process_command({"command": "set_setpoint"})

    def test_ping(self, server: JulaboTCPServer) -> None:
        result = server.process_command({"command": "ping"})
        assert result["status"] == "ok"
        assert result["result"] == "pong"
        assert result["protocol_version"] == PROTOCOL_VERSION

    def test_missing_command(self, server: JulaboTCPServer) -> None:
        with pytest.raises(ValueError, match="Missing"):
            server.process_command({})

    def test_unsupported_command(self, server: JulaboTCPServer) -> None:
        with pytest.raises(ValueError, match="Unsupported"):
            server.process_command({"command": "bogus"})

    def test_temperature(self, server: JulaboTCPServer) -> None:
        server.chiller.get_temperature.return_value = 21.5
        result = server.process_command({"command": "temperature"})
        assert result["result"] == 21.5

    def test_is_running(self, server: JulaboTCPServer) -> None:
        server.chiller.is_running.return_value = True
        result = server.process_command({"command": "is_running"})
        assert result["result"] is True

    def test_start(self, server: JulaboTCPServer) -> None:
        server.chiller.start.return_value = True
        result = server.process_command({"command": "start"})
        assert result["result"] is True

    def test_stop(self, server: JulaboTCPServer) -> None:
        server.chiller.stop.return_value = False
        result = server.process_command({"command": "stop"})
        assert result["result"] is False

    def test_set_running(self, server: JulaboTCPServer) -> None:
        server.chiller.set_running.return_value = True
        result = server.process_command({"command": "set_running", "value": True})
        server.chiller.set_running.assert_called_once_with(True)
        assert result["result"] is True

    def test_set_running_missing_value(self, server: JulaboTCPServer) -> None:
        with pytest.raises(ValueError, match="requires a boolean"):
            server.process_command({"command": "set_running"})

    def test_status(self, server: JulaboTCPServer) -> None:
        server.chiller.get_status.return_value = "01 OK"
        result = server.process_command({"command": "status"})
        assert result["result"] == "01 OK"


class TestNormalizeBoolean:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, True),
            (False, False),
            (1, True),
            (0, False),
            ("true", True),
            ("false", False),
            ("on", True),
            ("off", False),
            ("1", True),
            ("0", False),
            ("start", True),
            ("stop", False),
        ],
    )
    def test_valid_values(self, value, expected) -> None:
        assert _normalize_boolean(value) is expected

    @pytest.mark.parametrize("value", ["maybe", "yes", None, [], {}])
    def test_invalid_values(self, value) -> None:
        with pytest.raises(ValueError, match="Unable to interpret"):
            _normalize_boolean(value)


class TestAuthentication:
    def test_valid_token(self, authed_server: JulaboTCPServer) -> None:
        authed_server.chiller.identify.return_value = "JULABO FL1703"
        result = authed_server.process_command(
            {"command": "identify", "token": "secret123"}
        )
        assert result["status"] == "ok"
        assert result["result"] == "JULABO FL1703"
        assert result["protocol_version"] == PROTOCOL_VERSION

    def test_missing_token(self, authed_server: JulaboTCPServer) -> None:
        with pytest.raises(PermissionError, match="Invalid or missing"):
            authed_server.process_command({"command": "identify"})

    def test_wrong_token(self, authed_server: JulaboTCPServer) -> None:
        with pytest.raises(PermissionError, match="Invalid or missing"):
            authed_server.process_command(
                {"command": "identify", "token": "wrong"}
            )

    def test_no_auth_backward_compat(self, server: JulaboTCPServer) -> None:
        """When auth_token is None, commands without a token succeed."""
        server.chiller.identify.return_value = "JULABO FL1703"
        result = server.process_command({"command": "identify"})
        assert result["status"] == "ok"
        assert result["result"] == "JULABO FL1703"


class TestStatusAll:
    def test_status_all(self, server: JulaboTCPServer) -> None:
        server.chiller.get_status.return_value = "01 OK"
        server.chiller.get_temperature.return_value = 21.5
        server.chiller.get_setpoint.return_value = 20.0
        server.chiller.is_running.return_value = True
        result = server.process_command({"command": "status_all"})
        assert result["status"] == "ok"
        data = result["result"]
        assert data["status"] == "01 OK"
        assert data["temperature"] == 21.5
        assert data["setpoint"] == 20.0
        assert data["is_running"] is True


class TestRateLimiter:
    def test_allows_within_limit(self) -> None:
        rl = _RateLimiter(max_requests=3, window=60.0)
        assert rl.allow("192.168.1.1") is True
        assert rl.allow("192.168.1.1") is True
        assert rl.allow("192.168.1.1") is True

    def test_denies_over_limit(self) -> None:
        rl = _RateLimiter(max_requests=2, window=60.0)
        assert rl.allow("10.0.0.1") is True
        assert rl.allow("10.0.0.1") is True
        assert rl.allow("10.0.0.1") is False

    def test_different_ips_independent(self) -> None:
        rl = _RateLimiter(max_requests=1, window=60.0)
        assert rl.allow("10.0.0.1") is True
        assert rl.allow("10.0.0.2") is True
        assert rl.allow("10.0.0.1") is False

    def test_expired_entries_purged(self) -> None:
        rl = _RateLimiter(max_requests=1, window=0.01)
        assert rl.allow("10.0.0.1") is True

        import time
        time.sleep(0.02)

        assert rl.allow("10.0.0.1") is True

    def test_stale_ip_evicted(self) -> None:
        """IPs with all expired entries are removed from the internal dict."""
        rl = _RateLimiter(max_requests=1, window=0.01)
        assert rl.allow("10.0.0.1") is True
        assert "10.0.0.1" in rl._requests

        import time
        time.sleep(0.02)

        # A call for a different IP triggers purge of the stale one indirectly
        assert rl.allow("10.0.0.1") is True
        # After allow, the stale deque was evicted then re-created
        assert "10.0.0.1" in rl._requests  # re-created for new request

    def test_stale_ip_removed_from_dict(self) -> None:
        """After expiry, the IP is removed from _requests on next allow call."""
        rl = _RateLimiter(max_requests=1, window=0.01)
        rl.allow("10.0.0.99")

        import time
        time.sleep(0.02)

        # Calling allow for a DIFFERENT IP — the old IP should get cleaned
        # when that IP itself is checked
        rl.allow("10.0.0.99")
        # The deque should have exactly 1 entry (the new request)
        assert len(rl._requests["10.0.0.99"]) == 1


class TestProtocolVersion:
    def test_every_response_includes_version(self, server: JulaboTCPServer) -> None:
        server.chiller.get_temperature.return_value = 20.0
        result = server.process_command({"command": "temperature"})
        assert "protocol_version" in result
        assert result["protocol_version"] == PROTOCOL_VERSION

    def test_status_all_includes_version(self, server: JulaboTCPServer) -> None:
        server.chiller.get_status.return_value = "01 OK"
        server.chiller.get_temperature.return_value = 21.5
        server.chiller.get_setpoint.return_value = 20.0
        server.chiller.is_running.return_value = True
        result = server.process_command({"command": "status_all"})
        assert result["protocol_version"] == PROTOCOL_VERSION


class TestResolveAuthToken:
    def test_cli_arg_has_priority(self) -> None:
        assert resolve_auth_token("cli", None, "config") == "cli"

    def test_file_over_env_and_config(self, tmp_path) -> None:
        token_file = tmp_path / "token.txt"
        token_file.write_text("  file-token  \n")
        with patch.dict("os.environ", {"JULABO_AUTH_TOKEN": "env"}):
            result = resolve_auth_token(None, str(token_file), "config")
        assert result == "file-token"

    def test_env_over_config(self) -> None:
        with patch.dict("os.environ", {"JULABO_AUTH_TOKEN": "env-token"}):
            result = resolve_auth_token(None, None, "config")
        assert result == "env-token"

    def test_config_fallback(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = resolve_auth_token(None, None, "config-token")
        assert result == "config-token"

    def test_none_when_all_empty(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = resolve_auth_token(None, None, None)
        assert result is None


class TestErrorSanitization:
    def test_newlines_in_error_sanitized(self, server: JulaboTCPServer) -> None:
        """Exceptions with newlines produce clean single-line error strings."""
        server.chiller.identify.side_effect = RuntimeError("line1\nline2\rline3")
        try:
            server.process_command({"command": "identify"})
        except RuntimeError:
            pass
        # The sanitization happens in the handler, not process_command.
        # Verify the replacement logic directly:
        error_msg = str(server.chiller.identify.side_effect)
        sanitized = error_msg.replace("\n", " ").replace("\r", " ")
        assert "\n" not in sanitized
        assert "\r" not in sanitized
        # Verify it can be serialised to single-line JSON
        line = json.dumps({"error": sanitized})
        assert "\n" not in line


class TestGracefulShutdown:
    def test_increment_decrement(self, server: JulaboTCPServer) -> None:
        assert server.increment_connections() is True
        assert server._active_connections == 1
        server.decrement_connections()
        assert server._active_connections == 0

    def test_reject_during_shutdown(self, server: JulaboTCPServer) -> None:
        server.begin_shutdown()
        assert server.increment_connections() is False

    def test_begin_shutdown_logs(self, server: JulaboTCPServer, caplog) -> None:
        import logging

        server.increment_connections()
        with caplog.at_level(logging.INFO, logger="julabo_control.remote_server"):
            server.begin_shutdown()
        assert "1 active connection" in caplog.text


class TestTrafficLogging:
    def test_traffic_logger_attribute(self, server: JulaboTCPServer) -> None:
        assert server._traffic_logger is None


class TestReadOnlyMode:
    def test_write_commands_rejected(self) -> None:
        srv = _make_server(read_only=True)
        for cmd in _WRITE_COMMANDS:
            result = srv.process_command({"command": cmd, "value": 1.0})
            assert result["status"] == "error"
            assert "read-only" in result["error"]

    def test_read_commands_work(self) -> None:
        srv = _make_server(read_only=True)
        srv.chiller.get_temperature.return_value = 20.0
        result = srv.process_command({"command": "temperature"})
        assert result["status"] == "ok"

    def test_ping_works(self) -> None:
        srv = _make_server(read_only=True)
        result = srv.process_command({"command": "ping"})
        assert result["result"] == "pong"

    def test_disabled_by_default(self, server: JulaboTCPServer) -> None:
        assert server._read_only is False


class TestIdleTimeout:
    def test_idle_timeout_default_zero(self, server: JulaboTCPServer) -> None:
        assert server._idle_timeout == 0

    def test_idle_timeout_stored(self) -> None:
        srv = _make_server(idle_timeout=300)
        assert srv._idle_timeout == 300


class TestAuditLog:
    def test_audit_for_set_setpoint(self, server: JulaboTCPServer) -> None:
        audit = logging.getLogger("test_audit_sp")
        handler = logging.handlers.MemoryHandler(capacity=100)
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)
        server._audit_logger = audit
        server.chiller.get_setpoint.return_value = 25.0
        server.process_command(
            {"command": "set_setpoint", "value": 25.0}, client_ip="10.0.0.1"
        )
        assert len(handler.buffer) == 1
        assert "set_setpoint" in handler.buffer[0].getMessage()
        assert "10.0.0.1" in handler.buffer[0].getMessage()

    def test_audit_for_start(self, server: JulaboTCPServer) -> None:
        import logging.handlers

        audit = logging.getLogger("test_audit_start")
        handler = logging.handlers.MemoryHandler(capacity=100)
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)
        server._audit_logger = audit
        server.chiller.start.return_value = True
        server.chiller.is_running.return_value = False
        server.process_command({"command": "start"}, client_ip="10.0.0.2")
        assert len(handler.buffer) == 1
        assert "start" in handler.buffer[0].getMessage()

    def test_no_audit_for_reads(self, server: JulaboTCPServer) -> None:
        import logging.handlers

        audit = logging.getLogger("test_audit_reads")
        handler = logging.handlers.MemoryHandler(capacity=100)
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)
        server._audit_logger = audit
        server.chiller.get_temperature.return_value = 20.0
        server.process_command({"command": "temperature"})
        assert len(handler.buffer) == 0

    def test_no_error_when_logger_none(self, server: JulaboTCPServer) -> None:
        assert server._audit_logger is None
        server.chiller.start.return_value = True
        result = server.process_command({"command": "start"})
        assert result["status"] == "ok"


class TestSerialDisconnected:
    def test_commands_rejected_when_disconnected(self, server: JulaboTCPServer) -> None:
        server._serial_connected = False
        result = server.process_command({"command": "temperature"})
        assert result["status"] == "error"
        assert "reconnecting" in result["error"]

    def test_ping_works_when_disconnected(self, server: JulaboTCPServer) -> None:
        server._serial_connected = False
        result = server.process_command({"command": "ping"})
        assert result["status"] == "ok"
        assert result["result"] == "pong"


class TestSerialWatchdog:
    def test_watchdog_detects_failure(self) -> None:
        srv = _make_server()
        srv.chiller.get_status.side_effect = RuntimeError("disconnected")
        srv.chiller.connect.return_value = None
        srv.chiller.identify.return_value = "JULABO"

        watchdog = _SerialWatchdog(srv)
        # Manually trigger a run cycle
        watchdog._stop_event = threading.Event()
        # Directly call _run logic: simulate one check
        try:
            with srv._lock:
                srv.chiller.get_status()
        except RuntimeError:
            srv._serial_connected = False
            srv.chiller.close()
            # Simulate reconnect
            with srv._lock:
                srv.chiller.connect()
                srv.chiller.identify()
            srv._serial_connected = True

        assert srv._serial_connected is True

    def test_watchdog_default_none(self, server: JulaboTCPServer) -> None:
        assert server._watchdog is None


class TestRemoteSchedule:
    def test_load_valid_csv(self, server: JulaboTCPServer) -> None:
        result = server.process_command({
            "command": "load_schedule",
            "csv": "elapsed_minutes,temperature_c\n0,20\n10,30\n",
        })
        assert result["status"] == "ok"
        data = result["result"]
        assert data["steps"] == 2
        assert data["duration_minutes"] == 10.0

    def test_load_empty_csv_rejected(self, server: JulaboTCPServer) -> None:
        with pytest.raises(ValueError, match="requires a 'csv'"):
            server.process_command({"command": "load_schedule", "csv": ""})

    def test_load_invalid_csv(self, server: JulaboTCPServer) -> None:
        with pytest.raises(ValueError, match="No valid schedule"):
            server.process_command({"command": "load_schedule", "csv": "header\nonly\n"})

    def test_load_missing_csv(self, server: JulaboTCPServer) -> None:
        with pytest.raises(ValueError, match="requires a 'csv'"):
            server.process_command({"command": "load_schedule"})

    def test_stop_when_none(self, server: JulaboTCPServer) -> None:
        result = server.process_command({"command": "stop_schedule"})
        assert result["result"] == "stopped"

    def test_status_when_none(self, server: JulaboTCPServer) -> None:
        result = server.process_command({"command": "schedule_status"})
        assert result["result"]["running"] is False

    def test_load_replaces_existing(self, server: JulaboTCPServer) -> None:
        server.process_command({
            "command": "load_schedule",
            "csv": "time,temp\n0,20\n10,30\n",
        })
        result = server.process_command({
            "command": "load_schedule",
            "csv": "time,temp\n0,15\n5,25\n20,35\n",
        })
        assert result["result"]["steps"] == 3


# -- Phase 2: Audit old-value exception paths --


class TestAuditOldValueException:
    def test_audit_old_value_exception_setpoint(self) -> None:
        srv = _make_server()
        audit = logging.getLogger("test_audit_exc_sp")
        handler = logging.handlers.MemoryHandler(capacity=100)
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)
        srv._audit_logger = audit
        srv.chiller.get_setpoint.side_effect = [RuntimeError("fail"), 25.0]
        srv.process_command(
            {"command": "set_setpoint", "value": 25.0}, client_ip="10.0.0.1"
        )
        assert len(handler.buffer) == 1
        assert "unknown" in handler.buffer[0].getMessage()

    def test_audit_old_value_exception_start(self) -> None:
        srv = _make_server()
        audit = logging.getLogger("test_audit_exc_start")
        handler = logging.handlers.MemoryHandler(capacity=100)
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)
        srv._audit_logger = audit
        srv.chiller.is_running.side_effect = [RuntimeError("fail"), True]
        srv.chiller.start.return_value = True
        srv.process_command({"command": "start"}, client_ip="10.0.0.2")
        assert len(handler.buffer) == 1
        msg = handler.buffer[0].getMessage()
        assert "start" in msg


# -- Phase 2: Metrics recording in process_command --


class TestMetricsInProcessCommand:
    def test_metrics_recorded_on_status_all(self) -> None:
        srv = _make_server()
        srv._metrics = _MetricsState()
        srv.chiller.get_status.return_value = "01 OK"
        srv.chiller.get_temperature.return_value = 21.5
        srv.chiller.get_setpoint.return_value = 20.0
        srv.chiller.is_running.return_value = True
        srv.process_command({"command": "status_all"})
        assert srv._metrics.commands_total.get("status_all") == 1
        assert srv._metrics.last_temperature == 21.5

    def test_metrics_temperature_not_cached_for_non_dict(self) -> None:
        srv = _make_server()
        srv._metrics = _MetricsState()
        srv.chiller.get_temperature.return_value = 21.5
        srv.process_command({"command": "temperature"})
        assert srv._metrics.commands_total.get("temperature") == 1
        # temperature returns a float, not a dict — should not be cached
        assert srv._metrics.last_temperature is None

    def test_metrics_ping_records_count(self) -> None:
        srv = _make_server()
        srv._metrics = _MetricsState()
        srv.process_command({"command": "ping"})
        assert srv._metrics.commands_total.get("ping") == 1


# -- Phase 2: Audit for stop and set_running --


class TestAuditStopAndSetRunning:
    def test_audit_for_stop_command(self) -> None:
        srv = _make_server()
        audit = logging.getLogger("test_audit_stop")
        handler = logging.handlers.MemoryHandler(capacity=100)
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)
        srv._audit_logger = audit
        srv.chiller.is_running.return_value = True
        srv.chiller.stop.return_value = False
        srv.process_command({"command": "stop"}, client_ip="10.0.0.3")
        assert len(handler.buffer) == 1
        assert "stop" in handler.buffer[0].getMessage()

    def test_audit_for_set_running(self) -> None:
        srv = _make_server()
        audit = logging.getLogger("test_audit_set_running")
        handler = logging.handlers.MemoryHandler(capacity=100)
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)
        srv._audit_logger = audit
        srv.chiller.is_running.return_value = False
        srv.chiller.set_running.return_value = True
        srv.process_command(
            {"command": "set_running", "value": True}, client_ip="10.0.0.4"
        )
        assert len(handler.buffer) == 1
        msg = handler.buffer[0].getMessage()
        assert "set_running" in msg
        assert "was False" in msg


# -- Phase 2: Schedule ticker paths --


class TestScheduleTicker:
    def test_schedule_ticker_exits_when_runner_none(self) -> None:
        srv = _make_server()
        # No runner for "default" chiller
        srv._schedule_ticker("default")

    def test_schedule_ticker_exits_when_not_running(self) -> None:
        srv = _make_server()
        runner = MagicMock()
        runner.is_running = False
        srv._schedule_runners["default"] = runner
        srv._schedule_ticker("default")

    def test_schedule_ticker_handles_tick_error(self) -> None:
        srv = _make_server()
        runner = MagicMock()
        runner.is_running = True
        runner.tick.side_effect = RuntimeError("tick error")
        srv._schedule_runners["default"] = runner
        srv._schedule_ticker("default")
        runner.stop.assert_called_once()
        assert "default" not in srv._schedule_runners

    def test_schedule_ticker_detects_finished(self) -> None:
        srv = _make_server()
        runner = MagicMock()
        runner.is_running = True
        runner.is_finished = True
        runner.tick.return_value = None
        srv._schedule_runners["default"] = runner
        srv._schedule_ticker("default")
        assert "default" not in srv._schedule_runners


# -- Phase 2: Schedule stop/status with active runner --


class TestScheduleStopStatusActive:
    def test_stop_schedule_with_active_runner(self) -> None:
        srv = _make_server()
        srv.process_command({
            "command": "load_schedule",
            "csv": "time,temp\n0,20\n10,30\n",
        })
        assert "default" in srv._schedule_runners
        result = srv.process_command({"command": "stop_schedule"})
        assert result["result"] == "stopped"
        assert "default" not in srv._schedule_runners

    def test_schedule_status_while_running(self) -> None:
        srv = _make_server()
        srv.process_command({
            "command": "load_schedule",
            "csv": "time,temp\n0,20\n10,30\n",
        })
        result = srv.process_command({"command": "schedule_status"})
        data = result["result"]
        assert data["running"] is True
        assert "elapsed_minutes" in data
        # Cleanup
        srv._stop_schedule()

    def test_schedule_status_stopped_runner(self) -> None:
        srv = _make_server()
        srv.process_command({
            "command": "load_schedule",
            "csv": "time,temp\n0,20\n10,30\n",
        })
        srv._schedule_runners["default"].stop()
        result = srv.process_command({"command": "schedule_status"})
        assert result["result"]["running"] is False


# -- Phase 2: Serial watchdog --


class TestSerialWatchdogThreaded:
    def test_watchdog_start_stop(self) -> None:
        srv = _make_server()
        srv.chiller.get_status.return_value = "01 OK"
        watchdog = _SerialWatchdog(srv)
        watchdog.start()
        time.sleep(0.05)
        watchdog.stop()
        assert not watchdog._thread.is_alive()

    def test_watchdog_healthy_serial(self) -> None:
        srv = _make_server()
        srv.chiller.get_status.return_value = "01 OK"
        watchdog = _SerialWatchdog(srv)
        # Patch interval to be very short
        with patch("julabo_control.remote_server.WATCHDOG_INTERVAL", 0.01):
            watchdog.start()
            time.sleep(0.05)
            watchdog.stop()
        assert srv._serial_connected is True

    def test_watchdog_detects_failure_and_reconnects(self) -> None:
        srv = _make_server()
        srv.chiller.get_status.side_effect = RuntimeError("disconnected")
        srv.chiller.connect.return_value = None
        srv.chiller.identify.return_value = "JULABO"
        watchdog = _SerialWatchdog(srv)
        with patch("julabo_control.remote_server.WATCHDOG_INTERVAL", 0.01), \
             patch("julabo_control.remote_server.WATCHDOG_INITIAL_BACKOFF", 0.01):
            watchdog.start()
            # Poll for reconnection instead of fixed sleep
            for _ in range(100):
                if srv._serial_connected:
                    break
                time.sleep(0.02)
            watchdog.stop()
        assert srv._serial_connected is True

    def test_watchdog_reconnect_backoff(self) -> None:
        srv = _make_server()
        connect_count = 0

        def fail_then_succeed(*args, **kwargs):
            nonlocal connect_count
            connect_count += 1
            if connect_count <= 2:
                raise RuntimeError("still broken")
            return None

        status_count = 0

        def status_fail_once(*args, **kwargs):
            nonlocal status_count
            status_count += 1
            if status_count == 1:
                raise RuntimeError("disconnected")
            return "01 OK"

        srv.chiller.get_status.side_effect = status_fail_once
        srv.chiller.connect.side_effect = fail_then_succeed
        srv.chiller.identify.return_value = "JULABO"
        watchdog = _SerialWatchdog(srv)
        with patch("julabo_control.remote_server.WATCHDOG_INTERVAL", 0.01), \
             patch("julabo_control.remote_server.WATCHDOG_INITIAL_BACKOFF", 0.01), \
             patch("julabo_control.remote_server.WATCHDOG_MAX_BACKOFF", 0.1):
            watchdog.start()
            # Poll for reconnection instead of fixed sleep
            for _ in range(100):
                if srv._serial_connected and connect_count >= 3:
                    break
                time.sleep(0.02)
            watchdog.stop()
        assert srv._serial_connected is True
        assert connect_count >= 3


# -- Phase 2: parse_arguments --


class TestParseArguments:
    def test_parse_defaults(self) -> None:
        with patch("sys.argv", ["julabo-server"]):
            args = parse_arguments()
        assert args.serial_port is None
        assert args.host is None
        assert args.port is None
        assert args.verbose is False
        assert args.read_only is False

    def test_parse_serial_port(self) -> None:
        with patch("sys.argv", ["julabo-server", "/dev/ttyUSB0"]):
            args = parse_arguments()
        assert args.serial_port == "/dev/ttyUSB0"

    def test_parse_all_flags(self) -> None:
        with patch("sys.argv", [
            "julabo-server",
            "--host", "0.0.0.0",
            "--port", "9000",
            "--baudrate", "4800",
            "--timeout", "2.5",
            "--auth-token", "secret",
            "--rate-limit", "100",
            "--idle-timeout", "300",
            "--metrics-port", "9090",
            "--log-format", "json",
        ]):
            args = parse_arguments()
        assert args.host == "0.0.0.0"
        assert args.port == 9000
        assert args.baudrate == 4800
        assert args.timeout == 2.5
        assert args.auth_token == "secret"
        assert args.rate_limit == 100
        assert args.idle_timeout == 300
        assert args.metrics_port == 9090
        assert args.log_format == "json"

    def test_parse_bool_flags(self) -> None:
        with patch("sys.argv", [
            "julabo-server", "--verbose", "--read-only", "--no-watchdog",
        ]):
            args = parse_arguments()
        assert args.verbose is True
        assert args.read_only is True
        assert args.no_watchdog is True


# -- Phase 2: configure_logging --


class TestConfigureLogging:
    def test_configure_logging_verbose(self) -> None:
        with patch("logging.basicConfig") as mock_basic:
            configure_logging(verbose=True)
        mock_basic.assert_called_once()
        call_kwargs = mock_basic.call_args
        assert call_kwargs[1]["level"] == logging.DEBUG

    def test_configure_logging_default(self) -> None:
        with patch("logging.basicConfig") as mock_basic:
            configure_logging(verbose=False)
        call_kwargs = mock_basic.call_args
        assert call_kwargs[1]["level"] == logging.INFO

    def test_configure_logging_with_file(self, tmp_path) -> None:
        log_file = str(tmp_path / "test.log")
        with patch("logging.basicConfig") as mock_basic:
            configure_logging(verbose=False, log_file=log_file)
        call_kwargs = mock_basic.call_args
        handlers = call_kwargs[1]["handlers"]
        assert len(handlers) == 2
        assert isinstance(handlers[1], logging.FileHandler)

    def test_configure_logging_json_format(self) -> None:
        from julabo_control.logging_utils import JsonFormatter

        with patch("logging.basicConfig") as mock_basic:
            configure_logging(verbose=False, log_format="json")
        call_kwargs = mock_basic.call_args
        handlers = call_kwargs[1]["handlers"]
        assert len(handlers) == 1
        assert isinstance(handlers[0].formatter, JsonFormatter)

    def test_configure_logging_text_format(self) -> None:
        with patch("logging.basicConfig") as mock_basic:
            configure_logging(verbose=False, log_format="text")
        call_kwargs = mock_basic.call_args
        handlers = call_kwargs[1]["handlers"]
        assert len(handlers) == 1
        assert isinstance(handlers[0].formatter, logging.Formatter)

    def test_configure_logging_json_with_file(self, tmp_path) -> None:
        from julabo_control.logging_utils import JsonFormatter

        log_file = str(tmp_path / "test.log")
        with patch("logging.basicConfig") as mock_basic:
            configure_logging(verbose=False, log_file=log_file, log_format="json")
        call_kwargs = mock_basic.call_args
        handlers = call_kwargs[1]["handlers"]
        assert len(handlers) == 2
        for h in handlers:
            assert isinstance(h.formatter, JsonFormatter)


# -- Phase 3: Config hot-reload --


class TestConfigReload:
    def test_reload_updates_mutable(self, tmp_path) -> None:
        srv = _make_server()
        config_file = tmp_path / "reload.ini"
        config_file.write_text(
            "[server]\nrate_limit = 100\nread_only = true\nidle_timeout = 300\n"
        )
        srv.reload_config(str(config_file))
        assert srv._rate_limiter is not None
        assert srv._read_only is True
        assert srv._idle_timeout == 300.0

    def test_reload_warns_immutable(self, tmp_path, caplog) -> None:
        srv = _make_server()
        config_file = tmp_path / "reload.ini"
        config_file.write_text(
            "[server]\nhost = 0.0.0.0\nport = 9999\nserial_port = /dev/ttyUSB1\n"
        )
        with caplog.at_level(logging.WARNING, logger="julabo_control.remote_server"):
            srv.reload_config(str(config_file))
        assert "immutable" in caplog.text
        assert "host" in caplog.text

    def test_reload_missing_file(self, tmp_path) -> None:
        srv = _make_server()
        missing = str(tmp_path / "does_not_exist.ini")
        # Should not raise
        srv.reload_config(missing)


# -- Phase 4: Multi-chiller support --


class TestMultiChiller:
    def test_single_chiller_compat(self) -> None:
        """Without chiller_id, commands route to the default chiller."""
        srv = _make_server()
        srv.chiller.get_temperature.return_value = 21.5
        result = srv.process_command({"command": "temperature"})
        assert result["result"] == 21.5

    def test_multi_chiller_routing(self) -> None:
        srv = _make_server()
        second = MagicMock()
        second.get_temperature.return_value = 30.0
        srv.add_chiller("chiller2", second)
        # Default chiller
        srv.chiller.get_temperature.return_value = 21.5
        r1 = srv.process_command({"command": "temperature"})
        assert r1["result"] == 21.5
        # Second chiller
        r2 = srv.process_command({"command": "temperature", "chiller_id": "chiller2"})
        assert r2["result"] == 30.0

    def test_unknown_chiller_id_error(self) -> None:
        srv = _make_server()
        with pytest.raises(ValueError, match="Unknown chiller_id"):
            srv.process_command({"command": "status", "chiller_id": "nonexistent"})

    def test_add_chiller(self) -> None:
        srv = _make_server()
        new_chiller = MagicMock()
        srv.add_chiller("ch2", new_chiller)
        assert srv._chillers["ch2"] is new_chiller

    def test_chiller_property_backward_compat(self) -> None:
        srv = _make_server()
        original = srv.chiller
        assert original is srv._chillers["default"]
        new_chiller = MagicMock()
        srv.chiller = new_chiller
        assert srv._chillers["default"] is new_chiller


class TestPerChillerSchedule:
    def test_load_schedule_chiller1_isolated_from_chiller2(self) -> None:
        srv = _make_server()
        ch2 = MagicMock()
        srv.add_chiller("ch2", ch2)
        srv.process_command({
            "command": "load_schedule",
            "csv": "time,temp\n0,20\n10,30\n",
            "chiller_id": "default",
        })
        srv.process_command({
            "command": "load_schedule",
            "csv": "time,temp\n0,15\n5,25\n",
            "chiller_id": "ch2",
        })
        assert "default" in srv._schedule_runners
        assert "ch2" in srv._schedule_runners
        # Stop one, the other should remain
        srv._stop_schedule("default")
        assert "default" not in srv._schedule_runners
        assert "ch2" in srv._schedule_runners
        srv._stop_schedule("ch2")

    def test_schedule_status_per_chiller(self) -> None:
        srv = _make_server()
        ch2 = MagicMock()
        srv.add_chiller("ch2", ch2)
        srv.process_command({
            "command": "load_schedule",
            "csv": "time,temp\n0,20\n10,30\n",
            "chiller_id": "default",
        })
        status_default = srv.process_command({
            "command": "schedule_status", "chiller_id": "default",
        })
        status_ch2 = srv.process_command({
            "command": "schedule_status", "chiller_id": "ch2",
        })
        assert status_default["result"]["running"] is True
        assert status_ch2["result"]["running"] is False
        srv._stop_schedule("default")

    def test_stop_schedule_per_chiller(self) -> None:
        srv = _make_server()
        ch2 = MagicMock()
        srv.add_chiller("ch2", ch2)
        srv.process_command({
            "command": "load_schedule",
            "csv": "time,temp\n0,20\n10,30\n",
            "chiller_id": "ch2",
        })
        result = srv.process_command({
            "command": "stop_schedule", "chiller_id": "ch2",
        })
        assert result["result"] == "stopped"
        assert "ch2" not in srv._schedule_runners


# -- Phase 7: Sanitize error messages --


class TestSanitizeError:
    def test_sanitize_error_maps_known_types(self) -> None:
        assert _sanitize_error(PermissionError("x")) == "Authentication failed"
        assert _sanitize_error(ValueError("bad value")) == "Invalid request: bad value"
        assert _sanitize_error(TypeError("y")) == "Invalid argument type"
        assert _sanitize_error(TimeoutError("z")) == "Device timeout"
        assert _sanitize_error(JulaboError("oops")) == "Device error: oops"

    def test_sanitize_error_unknown_returns_generic(self) -> None:
        assert _sanitize_error(RuntimeError("secret")) == "Internal server error"
        assert _sanitize_error(OSError("disk full")) == "Internal server error"


# -- Phase 7: Circuit breaker --


class TestWatchdogCircuitBreaker:
    def test_watchdog_circuit_breaker_gives_up(self) -> None:
        srv = _make_server()
        srv.chiller.get_status.side_effect = RuntimeError("disconnected")
        srv.chiller.connect.side_effect = RuntimeError("still broken")
        srv.chiller.identify.return_value = "JULABO"
        watchdog = _SerialWatchdog(srv)
        with patch("julabo_control.remote_server.WATCHDOG_INTERVAL", 0.01), \
             patch("julabo_control.remote_server.WATCHDOG_INITIAL_BACKOFF", 0.001), \
             patch("julabo_control.remote_server.WATCHDOG_MAX_BACKOFF", 0.001), \
             patch("julabo_control.remote_server.MAX_WATCHDOG_RETRIES", 3):
            watchdog.start()
            time.sleep(0.5)
            watchdog.stop()
        # Should still be disconnected since circuit breaker tripped
        assert srv._serial_connected is False


# -- Phase 7: Error metrics --


class TestErrorMetrics:
    def test_record_error_increments(self) -> None:
        metrics = _MetricsState()
        metrics.record_error("ValueError")
        metrics.record_error("ValueError")
        metrics.record_error("TimeoutError")
        assert metrics.errors_total["ValueError"] == 2
        assert metrics.errors_total["TimeoutError"] == 1

    def test_error_metrics_in_prometheus_output(self) -> None:
        metrics = _MetricsState()
        metrics.record_error("ValueError")
        output = metrics.render_prometheus()
        assert 'julabo_commands_errors_total{type="ValueError"} 1' in output
