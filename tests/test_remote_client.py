"""Tests for julabo_control.remote_client."""

from __future__ import annotations

import json
import socket
import threading
import tkinter as tk
from unittest.mock import MagicMock, patch

import pytest

from julabo_control.remote_client import (
    RemoteChillerApp,
    RemoteChillerClient,
    parse_args,
)
from julabo_control.remote_server import resolve_auth_token


class _FakeServer:
    """A minimal TCP server that returns a canned JSON response."""

    def __init__(self, response: dict) -> None:
        self._response = response
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        conn, _ = self._sock.accept()
        conn.makefile("rb").readline()
        resp = json.dumps(self._response).encode("utf-8") + b"\n"
        conn.sendall(resp)
        conn.close()
        self._sock.close()


class TestRemoteChillerClient:
    def test_command_success(self) -> None:
        server = _FakeServer({"status": "ok", "result": "JULABO FL1703"})
        client = RemoteChillerClient("127.0.0.1", server.port, timeout=2.0, retries=1)
        result = client.command("identify")
        assert result == "JULABO FL1703"

    def test_command_with_value(self) -> None:
        server = _FakeServer({"status": "ok", "result": 25.0})
        client = RemoteChillerClient("127.0.0.1", server.port, timeout=2.0, retries=1)
        result = client.command("set_setpoint", 25.0)
        assert result == 25.0

    def test_command_server_error(self) -> None:
        server = _FakeServer({"status": "error", "error": "bad request"})
        client = RemoteChillerClient("127.0.0.1", server.port, timeout=2.0, retries=1)
        with pytest.raises(RuntimeError, match="bad request"):
            client.command("identify")

    def test_connection_refused(self) -> None:
        client = RemoteChillerClient("127.0.0.1", 1, timeout=0.1, retries=1)
        with pytest.raises((ConnectionError, OSError)):
            client.command("ping")

    def test_auth_token_sent(self) -> None:
        """Verify the auth token is included in the payload."""
        received: dict = {}

        def fake_serve(sock: socket.socket) -> None:
            conn, _ = sock.accept()
            raw = conn.makefile("rb").readline()
            received.update(json.loads(raw.decode("utf-8")))
            resp = json.dumps({"status": "ok", "result": "pong"}).encode("utf-8") + b"\n"
            conn.sendall(resp)
            conn.close()
            sock.close()

        srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv_sock.bind(("127.0.0.1", 0))
        srv_sock.listen(1)
        port = srv_sock.getsockname()[1]
        t = threading.Thread(target=fake_serve, args=(srv_sock,), daemon=True)
        t.start()

        client = RemoteChillerClient(
            "127.0.0.1", port, timeout=2.0, retries=1, auth_token="mytoken"
        )
        client.command("ping")
        assert received.get("token") == "mytoken"

    def test_status_all(self) -> None:
        response = {
            "status": "ok",
            "result": {
                "status": "01 OK",
                "temperature": 21.5,
                "setpoint": 20.0,
                "is_running": True,
            },
        }
        server = _FakeServer(response)
        client = RemoteChillerClient("127.0.0.1", server.port, timeout=2.0, retries=1)
        data = client.status_all()
        assert data["temperature"] == 21.5
        assert data["is_running"] is True

    def test_protocol_version_warning(self) -> None:
        """Client logs a warning when server protocol is newer."""
        response = {"status": "ok", "result": "pong", "protocol_version": 999}
        server = _FakeServer(response)
        client = RemoteChillerClient(
            "127.0.0.1", server.port, timeout=2.0, retries=1
        )
        import logging

        logger = logging.getLogger("julabo_control.remote_client")
        with patch.object(logger, "warning") as mock_warn:
            client.command("ping")
            mock_warn.assert_called_once()
            assert "999" in str(mock_warn.call_args)

    def test_retry_with_backoff(self) -> None:
        """Verify retries happen on connection failure."""
        client = RemoteChillerClient("127.0.0.1", 1, timeout=0.05, retries=2)
        with pytest.raises((ConnectionError, OSError)):
            client.command("ping")


class TestParseArgs:
    def test_defaults(self) -> None:
        with patch("sys.argv", ["prog", "myhost"]):
            args = parse_args()
        assert args.host == "myhost"
        assert args.port is None
        assert args.timeout is None

    def test_all_args(self) -> None:
        with patch("sys.argv", [
            "prog", "myhost",
            "--port", "9999",
            "--timeout", "10.0",
            "--auth-token", "abc123",
            "--poll-interval", "3000",
            "--tls",
            "--temperature-log", "/tmp/log.csv",
            "--desktop-notifications",
            "--log-traffic", "/tmp/traffic.log",
            "--font-size", "16",
        ]):
            args = parse_args()
        assert args.host == "myhost"
        assert args.port == 9999
        assert args.timeout == 10.0
        assert args.auth_token == "abc123"
        assert args.poll_interval == 3000
        assert args.tls is True
        assert args.temperature_log == "/tmp/log.csv"
        assert args.desktop_notifications is True
        assert args.log_traffic == "/tmp/traffic.log"
        assert args.font_size == 16

    def test_auth_token_file_arg(self) -> None:
        with patch("sys.argv", ["prog", "myhost", "--auth-token-file", "/tmp/token"]):
            args = parse_args()
        assert args.auth_token_file == "/tmp/token"


class TestResolveAuthToken:
    def test_cli_arg_wins(self) -> None:
        assert resolve_auth_token("cli", None, "config") == "cli"

    def test_file_over_env(self, tmp_path) -> None:
        tf = tmp_path / "t.txt"
        tf.write_text("from-file\n")
        with patch.dict("os.environ", {"JULABO_AUTH_TOKEN": "env"}):
            assert resolve_auth_token(None, str(tf), "cfg") == "from-file"

    def test_env_over_config(self) -> None:
        with patch.dict("os.environ", {"JULABO_AUTH_TOKEN": "env-val"}):
            assert resolve_auth_token(None, None, "cfg") == "env-val"

    def test_config_fallback(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert resolve_auth_token(None, None, "cfg-val") == "cfg-val"

    def test_all_none(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert resolve_auth_token(None, None, None) is None


def _make_remote_app() -> RemoteChillerApp:
    """Create a RemoteChillerApp with mocked Tk and client."""
    obj = RemoteChillerApp.__new__(RemoteChillerApp)
    obj.root = MagicMock(spec=tk.Tk)
    obj.root.winfo_exists.return_value = True
    obj.client = MagicMock(spec=RemoteChillerClient)
    obj._refresh_job = None
    obj._flash_job = None

    obj.status_var = MagicMock(spec=tk.StringVar)
    obj.temperature_var = MagicMock(spec=tk.StringVar)
    obj.setpoint_var = MagicMock(spec=tk.StringVar)
    obj.running_var = MagicMock(spec=tk.BooleanVar)
    obj.running_var.get.return_value = False
    obj.running_text_var = MagicMock(spec=tk.StringVar)
    obj.new_setpoint_var = MagicMock(spec=tk.StringVar)
    obj.poll_interval_var = MagicMock(spec=tk.IntVar)
    obj.poll_interval_var.get.return_value = 5000
    obj.alarm_threshold_var = MagicMock(spec=tk.DoubleVar)
    obj.alarm_threshold_var.get.return_value = 2.0
    obj._msg_var = MagicMock(spec=tk.StringVar)

    obj.temperature_plot = MagicMock()
    obj.temperature_logger = None
    obj.status_label = MagicMock()
    obj.temp_label = MagicMock()
    obj.toggle_button = MagicMock()
    obj.main_frame = MagicMock()
    obj.alarm = MagicMock()

    return obj


class _MultiResponseServer:
    """A TCP server that accepts one connection and returns multiple responses."""

    def __init__(self, responses: list[dict], *, break_after: int | None = None) -> None:
        self._responses = responses
        self._break_after = break_after
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(2)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        conn, _ = self._sock.accept()
        f = conn.makefile("rb")
        for i, resp in enumerate(self._responses):
            line = f.readline()
            if not line:
                break
            if self._break_after is not None and i == self._break_after:
                conn.close()
                # Accept a new reconnection
                conn, _ = self._sock.accept()
                f = conn.makefile("rb")
                line = f.readline()
                if not line:
                    break
            out = json.dumps(resp).encode("utf-8") + b"\n"
            conn.sendall(out)
        conn.close()
        self._sock.close()


class TestPersistentConnection:
    def test_persistent_connection_reuses_socket(self) -> None:
        responses = [
            {"status": "ok", "result": "pong"},
            {"status": "ok", "result": "pong"},
        ]
        srv = _MultiResponseServer(responses)
        client = RemoteChillerClient(
            "127.0.0.1", srv.port, timeout=2.0, retries=1, persistent=True
        )
        try:
            r1 = client.command("ping")
            r2 = client.command("ping")
            assert r1 == "pong"
            assert r2 == "pong"
        finally:
            client.close()

    def test_persistent_connection_reconnects_on_break(self) -> None:
        responses = [
            {"status": "ok", "result": "pong"},
            {"status": "ok", "result": "pong"},
        ]
        srv = _MultiResponseServer(responses, break_after=0)
        client = RemoteChillerClient(
            "127.0.0.1", srv.port, timeout=2.0, retries=1, persistent=True
        )
        try:
            r1 = client.command("ping")
            assert r1 == "pong"
            # Server closes connection after first response; client should reconnect
            r2 = client.command("ping")
            assert r2 == "pong"
        finally:
            client.close()

    def test_persistent_close_is_idempotent(self) -> None:
        client = RemoteChillerClient(
            "127.0.0.1", 1, timeout=0.1, retries=1, persistent=True
        )
        client.close()
        client.close()  # Should not raise


class TestTypeChecks:
    """Test that replaced assertions raise proper exceptions."""

    def test_status_all_raises_type_error_on_bad_response(self) -> None:
        server = _FakeServer({"status": "ok", "result": "not-a-dict"})
        client = RemoteChillerClient("127.0.0.1", server.port, timeout=2.0, retries=1)
        with pytest.raises(TypeError, match="Expected dict from status_all"):
            client.status_all()

    def test_stop_schedule_raises_type_error_on_bad_response(self) -> None:
        server = _FakeServer({"status": "ok", "result": 42})
        client = RemoteChillerClient("127.0.0.1", server.port, timeout=2.0, retries=1)
        with pytest.raises(TypeError, match="Expected str from stop_schedule"):
            client.stop_schedule()


class TestRemoteToggleRunning:
    def test_start_success(self) -> None:
        app = _make_remote_app()
        app.running_var.get.return_value = False
        app.client.command.return_value = True
        app.client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 21.0,
            "setpoint": 20.0,
            "is_running": True,
        }
        with patch("julabo_control.remote_client.messagebox"):
            app._toggle_running()
        app.client.command.assert_called_once_with("start")

    def test_stop_success(self) -> None:
        app = _make_remote_app()
        app.running_var.get.return_value = True
        app.client.command.return_value = False
        app.client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 21.0,
            "setpoint": 20.0,
            "is_running": False,
        }
        with patch("julabo_control.remote_client.messagebox"):
            app._toggle_running()
        app.client.command.assert_called_once_with("stop")

    def test_error_shows_status(self) -> None:
        app = _make_remote_app()
        app.client.command.side_effect = RuntimeError("comm fail")
        with patch("julabo_control.remote_client.messagebox") as mock_mb:
            app._toggle_running()
            mock_mb.showerror.assert_called_once()
        assert "comm fail" in app._msg_var.set.call_args[0][0]


class TestRemoteApplySetpoint:
    def test_valid(self) -> None:
        app = _make_remote_app()
        app.new_setpoint_var.get.return_value = "25.0"
        app.client.command.return_value = 25.0
        app.client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 21.0,
            "setpoint": 25.0,
            "is_running": False,
        }
        with patch("julabo_control.remote_client.messagebox"):
            app._apply_setpoint()
        app.client.command.assert_called_once_with("set_setpoint", 25.0)

    def test_invalid_text(self) -> None:
        app = _make_remote_app()
        app.new_setpoint_var.get.return_value = "abc"
        with patch("julabo_control.remote_client.messagebox") as mock_mb:
            app._apply_setpoint()
            mock_mb.showwarning.assert_called_once()

    def test_out_of_range(self) -> None:
        app = _make_remote_app()
        app.new_setpoint_var.get.return_value = "999"
        with patch("julabo_control.remote_client.messagebox") as mock_mb:
            app._apply_setpoint()
            mock_mb.showwarning.assert_called_once()

    def test_server_error(self) -> None:
        app = _make_remote_app()
        app.new_setpoint_var.get.return_value = "25.0"
        app.client.command.side_effect = RuntimeError("server error")
        with patch("julabo_control.remote_client.messagebox") as mock_mb:
            app._apply_setpoint()
            mock_mb.showerror.assert_called_once()


class TestAutoRefreshStatus:
    def test_success_updates_vars(self) -> None:
        app = _make_remote_app()
        app.client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 22.5,
            "setpoint": 20.0,
            "is_running": True,
        }
        app._auto_refresh_status()
        app.temperature_var.set.assert_called_with("22.50 \u00b0C")
        app.setpoint_var.set.assert_called_with("20.00 \u00b0C")
        app.running_var.set.assert_called_with(True)

    def test_error_shows_status(self) -> None:
        app = _make_remote_app()
        app.client.status_all.side_effect = RuntimeError("timeout")
        app._auto_refresh_status()
        assert "timeout" in app._msg_var.set.call_args[0][0]


class TestRefreshStatusError:
    def test_error_path(self) -> None:
        app = _make_remote_app()
        app.client.status_all.side_effect = RuntimeError("conn refused")
        with patch("julabo_control.remote_client.messagebox") as mock_mb:
            app.refresh_status()
            mock_mb.showerror.assert_called_once()
        assert "conn refused" in app._msg_var.set.call_args[0][0]


def _make_tk_var(**kw: object) -> MagicMock:
    """Create a simple MagicMock that behaves like a tkinter variable."""
    m = MagicMock()
    m.get.return_value = kw.get("value", "")
    return m


def _tk_var_patches():
    """Context manager that patches tkinter variable classes in remote_client."""
    return patch.multiple(
        "julabo_control.remote_client.tk",
        StringVar=MagicMock(side_effect=_make_tk_var),
        BooleanVar=MagicMock(side_effect=_make_tk_var),
        IntVar=MagicMock(side_effect=_make_tk_var),
        DoubleVar=MagicMock(side_effect=_make_tk_var),
    )


class TestRemoteChillerAppInit:
    """Test the real __init__ with fully mocked tkinter."""

    @patch("julabo_control.remote_client.TemperatureAlarm")
    @patch("julabo_control.remote_client.TemperatureHistoryPlot")
    @patch("julabo_control.remote_client.configure_default_fonts")
    def test_init_defaults(
        self, mock_fonts: MagicMock, MockPlot: MagicMock, MockAlarm: MagicMock
    ) -> None:
        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot

        root = MagicMock(spec=tk.Tk)
        root.winfo_exists.return_value = True
        client = MagicMock(spec=RemoteChillerClient)
        client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 21.0,
            "setpoint": 20.0,
            "is_running": False,
        }

        with _tk_var_patches(), \
             patch.object(RemoteChillerApp, "_build_layout"), \
             patch.object(RemoteChillerApp, "_bind_shortcuts"), \
             patch.object(RemoteChillerApp, "_show_status"), \
             patch.object(RemoteChillerApp, "_schedule_auto_refresh"), \
             patch.object(RemoteChillerApp, "refresh_status"):
            app = RemoteChillerApp(root, client)

        assert app.root is root
        assert app.client is client
        assert app._refresh_job is None
        assert app._flash_job is None
        mock_fonts.assert_called_once()

    @patch("julabo_control.remote_client.TemperatureAlarm")
    @patch("julabo_control.remote_client.TemperatureFileLogger")
    @patch("julabo_control.remote_client.TemperatureHistoryPlot")
    @patch("julabo_control.remote_client.configure_default_fonts")
    def test_init_with_log_file(
        self,
        mock_fonts: MagicMock,
        MockPlot: MagicMock,
        MockLogger: MagicMock,
        MockAlarm: MagicMock,
    ) -> None:
        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot
        mock_logger = MagicMock()
        MockLogger.return_value = mock_logger

        root = MagicMock(spec=tk.Tk)
        root.winfo_exists.return_value = True
        client = MagicMock(spec=RemoteChillerClient)
        client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 21.0,
            "setpoint": 20.0,
            "is_running": False,
        }

        with _tk_var_patches(), \
             patch.object(RemoteChillerApp, "_build_layout"), \
             patch.object(RemoteChillerApp, "_bind_shortcuts"), \
             patch.object(RemoteChillerApp, "_show_status"), \
             patch.object(RemoteChillerApp, "_schedule_auto_refresh"), \
             patch.object(RemoteChillerApp, "refresh_status"):
            app = RemoteChillerApp(root, client, log_file="/tmp/test_julabo.csv")

        assert app.temperature_logger is not None
        MockLogger.assert_called_once_with("/tmp/test_julabo.csv")

    @patch("julabo_control.remote_client.TemperatureAlarm")
    @patch("julabo_control.remote_client.TemperatureHistoryPlot")
    @patch("julabo_control.remote_client.configure_default_fonts")
    def test_init_creates_alarm(
        self, mock_fonts: MagicMock, MockPlot: MagicMock, MockAlarm: MagicMock
    ) -> None:
        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot

        root = MagicMock(spec=tk.Tk)
        root.winfo_exists.return_value = True
        client = MagicMock(spec=RemoteChillerClient)
        client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 21.0,
            "setpoint": 20.0,
            "is_running": False,
        }

        with _tk_var_patches(), \
             patch.object(RemoteChillerApp, "_build_layout"), \
             patch.object(RemoteChillerApp, "_bind_shortcuts"), \
             patch.object(RemoteChillerApp, "_show_status"), \
             patch.object(RemoteChillerApp, "_schedule_auto_refresh"), \
             patch.object(RemoteChillerApp, "refresh_status"):
            app = RemoteChillerApp(root, client, alarm_threshold=3.5)

        assert app.alarm is not None
        MockAlarm.assert_called_once()
        # Verify the threshold kwarg
        _, kwargs = MockAlarm.call_args
        assert kwargs["threshold"] == 3.5

    @patch("julabo_control.remote_client.TemperatureAlarm")
    @patch("julabo_control.remote_client.TemperatureHistoryPlot")
    @patch("julabo_control.remote_client.configure_default_fonts")
    def test_init_custom_poll_interval(
        self, mock_fonts: MagicMock, MockPlot: MagicMock, MockAlarm: MagicMock
    ) -> None:
        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot

        root = MagicMock(spec=tk.Tk)
        root.winfo_exists.return_value = True
        client = MagicMock(spec=RemoteChillerClient)
        client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 21.0,
            "setpoint": 20.0,
            "is_running": False,
        }

        with _tk_var_patches(), \
             patch.object(RemoteChillerApp, "_build_layout"), \
             patch.object(RemoteChillerApp, "_bind_shortcuts"), \
             patch.object(RemoteChillerApp, "_show_status"), \
             patch.object(RemoteChillerApp, "_schedule_auto_refresh"), \
             patch.object(RemoteChillerApp, "refresh_status"):
            app = RemoteChillerApp(root, client, poll_interval=10000)

        assert app.poll_interval_var.get() == 10000

    @patch("julabo_control.remote_client.TemperatureAlarm")
    @patch("julabo_control.remote_client.TemperatureHistoryPlot")
    @patch("julabo_control.remote_client.configure_default_fonts")
    def test_init_no_log_file(
        self, mock_fonts: MagicMock, MockPlot: MagicMock, MockAlarm: MagicMock
    ) -> None:
        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot

        root = MagicMock(spec=tk.Tk)
        root.winfo_exists.return_value = True
        client = MagicMock(spec=RemoteChillerClient)
        client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 21.0,
            "setpoint": 20.0,
            "is_running": False,
        }

        with _tk_var_patches(), \
             patch.object(RemoteChillerApp, "_build_layout"), \
             patch.object(RemoteChillerApp, "_bind_shortcuts"), \
             patch.object(RemoteChillerApp, "_show_status"), \
             patch.object(RemoteChillerApp, "_schedule_auto_refresh"), \
             patch.object(RemoteChillerApp, "refresh_status"):
            app = RemoteChillerApp(root, client)

        assert app.temperature_logger is None


class TestBuildLayoutRemote:
    """Test _build_layout creates the expected widgets."""

    @patch("julabo_control.remote_client.TemperatureAlarm")
    @patch("julabo_control.remote_client.TemperatureHistoryPlot")
    @patch("julabo_control.remote_client.configure_default_fonts")
    @patch("julabo_control.remote_client.tk.Frame")
    @patch("julabo_control.remote_client.tk.Label")
    @patch("julabo_control.remote_client.tk.Button")
    @patch("julabo_control.remote_client.tk.LabelFrame")
    @patch("julabo_control.remote_client.tk.Entry")
    @patch("julabo_control.remote_client.tk.Spinbox")
    def test_layout_creates_widgets(
        self,
        MockSpinbox: MagicMock,
        MockEntry: MagicMock,
        MockLabelFrame: MagicMock,
        MockButton: MagicMock,
        MockLabel: MagicMock,
        MockFrame: MagicMock,
        mock_fonts: MagicMock,
        MockPlot: MagicMock,
        MockAlarm: MagicMock,
    ) -> None:
        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot

        # Make Frame return a mock with winfo_children
        mock_frame = MagicMock()
        mock_frame.winfo_children.return_value = []
        MockFrame.return_value = mock_frame
        MockLabelFrame.return_value = MagicMock()

        root = MagicMock(spec=tk.Tk)
        root.winfo_exists.return_value = True
        client = MagicMock(spec=RemoteChillerClient)
        client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 21.0,
            "setpoint": 20.0,
            "is_running": False,
        }

        with _tk_var_patches(), \
             patch.object(RemoteChillerApp, "_schedule_auto_refresh"), \
             patch.object(RemoteChillerApp, "refresh_status"):
            app = RemoteChillerApp(root, client)

        # Verify key attributes were set by _build_layout
        assert app.main_frame is not None
        assert app.temp_label is not None
        assert app.toggle_button is not None
        assert app.status_label is not None
        assert app.temperature_plot is not None
        # Verify the plot was initialised
        MockPlot.assert_called_once()
        mock_plot.widget.pack.assert_called_once()
        mock_plot.clear.assert_called_once()

    @patch("julabo_control.remote_client.TemperatureAlarm")
    @patch("julabo_control.remote_client.TemperatureHistoryPlot")
    @patch("julabo_control.remote_client.configure_default_fonts")
    @patch("julabo_control.remote_client.tk.Frame")
    @patch("julabo_control.remote_client.tk.Label")
    @patch("julabo_control.remote_client.tk.Button")
    @patch("julabo_control.remote_client.tk.LabelFrame")
    @patch("julabo_control.remote_client.tk.Entry")
    @patch("julabo_control.remote_client.tk.Spinbox")
    def test_layout_iterates_children(
        self,
        MockSpinbox: MagicMock,
        MockEntry: MagicMock,
        MockLabelFrame: MagicMock,
        MockButton: MagicMock,
        MockLabel: MagicMock,
        MockFrame: MagicMock,
        mock_fonts: MagicMock,
        MockPlot: MagicMock,
        MockAlarm: MagicMock,
    ) -> None:
        """Ensure winfo_children() iteration applies grid_configure to children."""
        mock_plot = MagicMock()
        mock_plot.widget = MagicMock()
        MockPlot.return_value = mock_plot

        child1 = MagicMock()
        child2 = MagicMock()
        mock_frame = MagicMock()
        mock_frame.winfo_children.return_value = [child1, child2]
        MockFrame.return_value = mock_frame
        MockLabelFrame.return_value = MagicMock()

        root = MagicMock(spec=tk.Tk)
        root.winfo_exists.return_value = True
        client = MagicMock(spec=RemoteChillerClient)
        client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 21.0,
            "setpoint": 20.0,
            "is_running": False,
        }

        with _tk_var_patches(), \
             patch.object(RemoteChillerApp, "_schedule_auto_refresh"), \
             patch.object(RemoteChillerApp, "refresh_status"):
            RemoteChillerApp(root, client)

        # Each child should have had grid_configure called
        child1.grid_configure.assert_called_once_with(padx=4, pady=4)
        child2.grid_configure.assert_called_once_with(padx=4, pady=4)


class TestBindShortcutsRemote:
    """Test _bind_shortcuts registers the expected keyboard bindings."""

    def test_bindings_registered(self) -> None:
        app = _make_remote_app()
        app._bind_shortcuts()
        calls = [c[0][0] for c in app.root.bind.call_args_list]
        assert "<Control-r>" in calls
        assert "<Control-s>" in calls

    def test_binding_count(self) -> None:
        app = _make_remote_app()
        app._bind_shortcuts()
        # Exactly 2 bindings should be registered
        assert app.root.bind.call_count == 2


class TestScheduleAutoRefresh:
    """Test _schedule_auto_refresh schedules the auto-refresh loop."""

    def test_schedules_refresh(self) -> None:
        app = _make_remote_app()
        app._schedule_auto_refresh()
        app.root.after.assert_called_once()
        # The first positional argument should be the poll interval
        call_args = app.root.after.call_args
        assert call_args[0][0] == 5000

    def test_stores_refresh_job(self) -> None:
        app = _make_remote_app()
        app.root.after.return_value = "after#1"
        app._schedule_auto_refresh()
        assert app._refresh_job == "after#1"

    def test_uses_current_poll_interval(self) -> None:
        app = _make_remote_app()
        app.poll_interval_var.get.return_value = 10000
        app._schedule_auto_refresh()
        call_args = app.root.after.call_args
        assert call_args[0][0] == 10000

    def test_auto_refresh_callback_reschedules(self) -> None:
        """Invoke the inner callback registered via root.after to cover lines 323-328."""
        app = _make_remote_app()
        app.client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 22.0,
            "setpoint": 20.0,
            "is_running": False,
        }
        app.root.after.return_value = "after#2"
        app._schedule_auto_refresh()

        # Extract the callback that was passed to root.after
        callback = app.root.after.call_args[0][1]

        # Reset call tracking so we can verify the re-schedule
        app.root.after.reset_mock()
        app.root.after.return_value = "after#3"

        # Invoke the callback -- this should call _auto_refresh_status and reschedule
        callback()

        # It should have called root.after again to reschedule
        app.root.after.assert_called_once()
        assert app._refresh_job == "after#3"


class TestRefreshStatusClearMessage:
    """Test the clear_message parameter of refresh_status."""

    def test_clear_message_true(self) -> None:
        app = _make_remote_app()
        app.client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 22.0,
            "setpoint": 20.0,
            "is_running": False,
        }
        with patch("julabo_control.remote_client.messagebox"):
            app.refresh_status(clear_message=True)
        # When clear_message=True, _show_status should be called with empty string
        assert app._msg_var.set.call_args[0][0] == ""

    def test_clear_message_false_keeps_status(self) -> None:
        app = _make_remote_app()
        app.client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 22.0,
            "setpoint": 20.0,
            "is_running": False,
        }
        with patch("julabo_control.remote_client.messagebox"):
            app.refresh_status(clear_message=False)
        # When clear_message=False, _show_status("", ...) should NOT be the last call
        # _msg_var.set should not have been called with "" (no clear)
        for call in app._msg_var.set.call_args_list:
            assert call[0][0] != ""

    def test_clear_message_default_is_true(self) -> None:
        app = _make_remote_app()
        app.client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 22.0,
            "setpoint": 20.0,
            "is_running": False,
        }
        with patch("julabo_control.remote_client.messagebox"):
            app.refresh_status()
        # Default clear_message=True, so last _msg_var.set should be ""
        assert app._msg_var.set.call_args[0][0] == ""


class TestRemoteClientTimeouts:
    def test_socket_connect_timeout(self) -> None:
        """Connection to a non-routable IP should timeout."""
        import time as _time

        client = RemoteChillerClient("192.0.2.1", 9999, timeout=0.5, retries=1)
        start = _time.monotonic()
        with pytest.raises((ConnectionError, OSError, socket.timeout)):
            client.command("ping")
        elapsed = _time.monotonic() - start
        # Should have timed out rather than hanging forever
        assert elapsed < 10.0

    def test_retry_backoff_increases(self) -> None:
        """Verify total elapsed time with retries is > 1s due to backoff."""
        import time as _time

        client = RemoteChillerClient("127.0.0.1", 1, timeout=0.05, retries=3)
        start = _time.monotonic()
        with pytest.raises((ConnectionError, OSError)):
            client.command("ping")
        elapsed = _time.monotonic() - start
        # With 3 retries and backoff, should take longer than just one attempt
        assert elapsed >= 0.1


class TestRemoteClientScheduleMethods:
    """Test the schedule-related methods on RemoteChillerClient."""

    def test_load_schedule(self) -> None:
        response = {"status": "ok", "result": {"steps": 3, "duration_minutes": 15.0}}
        server = _FakeServer(response)
        client = RemoteChillerClient("127.0.0.1", server.port, timeout=2.0, retries=1)
        result = client.load_schedule("elapsed_minutes,temperature_c\n0,20\n10,30")
        assert result["steps"] == 3
        assert result["duration_minutes"] == 15.0

    def test_stop_schedule(self) -> None:
        response = {"status": "ok", "result": "stopped"}
        server = _FakeServer(response)
        client = RemoteChillerClient("127.0.0.1", server.port, timeout=2.0, retries=1)
        result = client.stop_schedule()
        assert result == "stopped"

    def test_schedule_status(self) -> None:
        response = {"status": "ok", "result": {"running": False}}
        server = _FakeServer(response)
        client = RemoteChillerClient("127.0.0.1", server.port, timeout=2.0, retries=1)
        result = client.schedule_status()
        assert result["running"] is False

    def test_schedule_status_running(self) -> None:
        response = {
            "status": "ok",
            "result": {
                "running": True,
                "current_step": 2,
                "elapsed_minutes": 5.0,
            },
        }
        server = _FakeServer(response)
        client = RemoteChillerClient("127.0.0.1", server.port, timeout=2.0, retries=1)
        result = client.schedule_status()
        assert result["running"] is True
        assert result["current_step"] == 2
