"""Tests for julabo_control.web."""

from __future__ import annotations

import json
import socket
import threading
import urllib.request
from unittest.mock import MagicMock

import pytest

from julabo_control.web import JulaboWebServer


@pytest.fixture
def web_server():
    """Start a real JulaboWebServer with a mock client."""
    client = MagicMock()
    client.status_all.return_value = {
        "status": "01 OK",
        "temperature": 21.5,
        "setpoint": 20.0,
        "is_running": True,
    }
    server = JulaboWebServer(("127.0.0.1", 0), client)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, client
    server.shutdown()
    thread.join(timeout=5)


def _url(server, path: str) -> str:
    port = server.server_address[1]
    return f"http://127.0.0.1:{port}{path}"


@pytest.fixture
def auth_web_server():
    """Start a JulaboWebServer that requires a web auth token."""
    client = MagicMock()
    client.status_all.return_value = {
        "status": "01 OK", "temperature": 21.5, "setpoint": 20.0, "is_running": True,
    }
    server = JulaboWebServer(("127.0.0.1", 0), client, web_auth_token="s3cret")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, client
    server.shutdown()
    thread.join(timeout=5)


class TestWebAuth:
    def test_api_status_without_token_is_401(self, auth_web_server) -> None:
        server, client = auth_web_server
        try:
            urllib.request.urlopen(_url(server, "/api/status"), timeout=5)
            raise AssertionError("Should have raised 401")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        client.status_all.assert_not_called()

    def test_api_status_with_header_token(self, auth_web_server) -> None:
        server, _ = auth_web_server
        req = urllib.request.Request(
            _url(server, "/api/status"), headers={"X-Auth-Token": "s3cret"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["temperature"] == 21.5

    def test_api_status_with_query_token(self, auth_web_server) -> None:
        server, _ = auth_web_server
        with urllib.request.urlopen(
            _url(server, "/api/status?token=s3cret"), timeout=5
        ) as resp:
            assert resp.status == 200

    def test_wrong_token_rejected(self, auth_web_server) -> None:
        server, _ = auth_web_server
        req = urllib.request.Request(
            _url(server, "/api/status"), headers={"X-Auth-Token": "wrong"}
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("Should have raised 401")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

    def test_setpoint_post_requires_token(self, auth_web_server) -> None:
        server, client = auth_web_server
        req = urllib.request.Request(
            _url(server, "/api/setpoint"), data=b'{"value": 25}', method="POST"
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("Should have raised 401")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        client.command.assert_not_called()

    def test_root_and_health_are_public(self, auth_web_server) -> None:
        server, _ = auth_web_server
        with urllib.request.urlopen(_url(server, "/"), timeout=5) as resp:
            assert resp.status == 200
        with urllib.request.urlopen(_url(server, "/api/health"), timeout=5) as resp:
            assert resp.status == 200


class TestBodyLimit:
    def test_oversized_body_rejected(self, web_server) -> None:
        server, client = web_server
        port = server.server_address[1]
        # Advertise an over-limit Content-Length via a raw request; the server
        # rejects with 413 before reading the (unsent) body.
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            request = (
                "POST /api/setpoint HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {300 * 1024}\r\n"
                "\r\n"
            )
            sock.sendall(request.encode())
            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                resp += chunk
        finally:
            sock.close()
        assert b"413" in resp.split(b"\r\n", 1)[0]
        client.command.assert_not_called()


class TestHistoryRecorder:
    def test_records_to_db(self) -> None:
        from julabo_control.db import TemperatureDB
        from julabo_control.web import _HistoryRecorder

        client = MagicMock()
        client.status_all.return_value = {"temperature": 21.0, "setpoint": 20.0}
        db = TemperatureDB(":memory:")
        recorder = _HistoryRecorder(client, db, interval=0.05)
        recorder.start()
        deadline = 3.0
        while not db.query_recent(60) and deadline > 0:
            import time as _t

            _t.sleep(0.05)
            deadline -= 0.05
        recorder.stop()
        rows = db.query_recent(60)
        assert rows and rows[0]["temperature"] == 21.0
        db.close()

    def test_record_failure_is_swallowed(self) -> None:
        from julabo_control.web import _HistoryRecorder

        client = MagicMock()
        client.status_all.side_effect = RuntimeError("down")
        db = MagicMock()
        recorder = _HistoryRecorder(client, db, interval=0.05)
        recorder.start()
        import time as _t

        _t.sleep(0.15)
        recorder.stop()  # must not raise despite client errors
        db.record.assert_not_called()


class TestWebServer:
    def test_html_served_at_root(self, web_server) -> None:
        server, _ = web_server
        with urllib.request.urlopen(_url(server, "/"), timeout=5) as resp:
            assert resp.status == 200
            body = resp.read().decode()
            assert "Julabo" in body
            assert "<html" in body

    def test_404_on_unknown(self, web_server) -> None:
        server, _ = web_server
        try:
            urllib.request.urlopen(_url(server, "/nope"), timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404

    def test_api_status(self, web_server) -> None:
        server, client = web_server
        with urllib.request.urlopen(_url(server, "/api/status"), timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["temperature"] == 21.5
        assert data["setpoint"] == 20.0
        assert data["is_running"] is True
        client.status_all.assert_called()

    def test_api_setpoint(self, web_server) -> None:
        server, client = web_server
        body = json.dumps({"value": 25.0}).encode()
        req = urllib.request.Request(
            _url(server, "/api/setpoint"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"
        client.command.assert_called_with("set_setpoint", 25.0)

    def test_api_start(self, web_server) -> None:
        server, client = web_server
        req = urllib.request.Request(
            _url(server, "/api/start"), method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"
        client.command.assert_called_with("start")

    def test_api_stop(self, web_server) -> None:
        server, client = web_server
        req = urllib.request.Request(
            _url(server, "/api/stop"), method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"
        client.command.assert_called_with("stop")

    def test_api_status_error(self, web_server) -> None:
        server, client = web_server
        client.status_all.side_effect = RuntimeError("connection failed")
        try:
            urllib.request.urlopen(_url(server, "/api/status"), timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 500
            data = json.loads(exc.read())
            assert "error" in data

    def test_api_setpoint_bad_json(self, web_server) -> None:
        server, _ = web_server
        req = urllib.request.Request(
            _url(server, "/api/setpoint"),
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            data = json.loads(exc.read())
            assert "error" in data

    def test_api_setpoint_missing_value(self, web_server) -> None:
        server, _ = web_server
        body = json.dumps({}).encode()
        req = urllib.request.Request(
            _url(server, "/api/setpoint"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            data = json.loads(exc.read())
            assert "Missing" in data["error"]

    def test_api_setpoint_exception(self, web_server) -> None:
        server, client = web_server
        client.command.side_effect = RuntimeError("serial failure")
        body = json.dumps({"value": 25.0}).encode()
        req = urllib.request.Request(
            _url(server, "/api/setpoint"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 500

    def test_api_start_exception(self, web_server) -> None:
        server, client = web_server
        client.command.side_effect = RuntimeError("serial failure")
        req = urllib.request.Request(
            _url(server, "/api/start"), method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 500

    def test_api_stop_exception(self, web_server) -> None:
        server, client = web_server
        client.command.side_effect = RuntimeError("serial failure")
        req = urllib.request.Request(
            _url(server, "/api/stop"), method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 500

    def test_api_post_unknown_path(self, web_server) -> None:
        server, _ = web_server
        req = urllib.request.Request(
            _url(server, "/api/nope"), method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404

    def test_empty_content_length_body(self, web_server) -> None:
        server, client = web_server
        # POST /api/start with Content-Length: 0 — should still work
        req = urllib.request.Request(
            _url(server, "/api/start"),
            data=b"",
            method="POST",
            headers={"Content-Length": "0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"


class TestAPIVersioning:
    def test_v1_status_path_works(self, web_server) -> None:
        server, client = web_server
        with urllib.request.urlopen(_url(server, "/api/v1/status"), timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["temperature"] == 21.5

    def test_legacy_status_path_still_works(self, web_server) -> None:
        server, client = web_server
        with urllib.request.urlopen(_url(server, "/api/status"), timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["temperature"] == 21.5

    def test_v1_setpoint_path_works(self, web_server) -> None:
        server, client = web_server
        body = json.dumps({"value": 25.0}).encode()
        req = urllib.request.Request(
            _url(server, "/api/v1/setpoint"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"

    def test_openapi_json_served(self, web_server) -> None:
        server, _ = web_server
        with urllib.request.urlopen(
            _url(server, "/api/v1/openapi.json"), timeout=5
        ) as resp:
            data = json.loads(resp.read())
        assert data["openapi"] == "3.0.3"
        assert "paths" in data

    def test_openapi_spec_has_setpoint_constraints(self, web_server) -> None:
        server, _ = web_server
        with urllib.request.urlopen(
            _url(server, "/api/v1/openapi.json"), timeout=5
        ) as resp:
            data = json.loads(resp.read())
        sp_schema = (
            data["paths"]["/api/v1/setpoint"]["post"]["requestBody"]
            ["content"]["application/json"]["schema"]["properties"]["value"]
        )
        from julabo_control.core import SETPOINT_MAX, SETPOINT_MIN
        assert sp_schema["minimum"] == SETPOINT_MIN
        assert sp_schema["maximum"] == SETPOINT_MAX


class TestSetpointValidation:
    def test_setpoint_below_minimum_returns_400(self, web_server) -> None:
        server, _ = web_server
        body = json.dumps({"value": -999}).encode()
        req = urllib.request.Request(
            _url(server, "/api/setpoint"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            data = json.loads(exc.read())
            assert "Setpoint" in data["error"]

    def test_setpoint_above_maximum_returns_400(self, web_server) -> None:
        server, _ = web_server
        body = json.dumps({"value": 999}).encode()
        req = urllib.request.Request(
            _url(server, "/api/setpoint"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            data = json.loads(exc.read())
            assert "Setpoint" in data["error"]

    def test_setpoint_non_numeric_returns_400(self, web_server) -> None:
        server, _ = web_server
        body = json.dumps({"value": "not-a-number"}).encode()
        req = urllib.request.Request(
            _url(server, "/api/setpoint"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            data = json.loads(exc.read())
            assert "numeric" in data["error"].lower() or "Invalid" in data["error"]

    def test_setpoint_at_boundary_accepted(self, web_server) -> None:
        server, client = web_server
        from julabo_control.core import SETPOINT_MAX, SETPOINT_MIN

        for value in [SETPOINT_MIN, SETPOINT_MAX]:
            body = json.dumps({"value": value}).encode()
            req = urllib.request.Request(
                _url(server, "/api/setpoint"),
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            assert data["status"] == "ok"


class TestScheduleWebUI:
    def test_schedule_upload_success(self, web_server) -> None:
        server, client = web_server
        client.load_schedule.return_value = {"steps": 2, "duration_minutes": 10.0}
        body = json.dumps({"csv": "time,temp\n0,20\n10,30\n"}).encode()
        req = urllib.request.Request(
            _url(server, "/api/schedule"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"
        assert data["result"]["steps"] == 2
        client.load_schedule.assert_called_once()

    def test_schedule_upload_missing_csv(self, web_server) -> None:
        server, _ = web_server
        body = json.dumps({}).encode()
        req = urllib.request.Request(
            _url(server, "/api/schedule"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            data = json.loads(exc.read())
            assert "Missing" in data["error"]

    def test_schedule_upload_error(self, web_server) -> None:
        server, client = web_server
        client.load_schedule.side_effect = RuntimeError("bad csv")
        body = json.dumps({"csv": "bad"}).encode()
        req = urllib.request.Request(
            _url(server, "/api/schedule"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 500

    def test_schedule_stop(self, web_server) -> None:
        server, client = web_server
        client.stop_schedule.return_value = "stopped"
        req = urllib.request.Request(
            _url(server, "/api/schedule"), method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"
        client.stop_schedule.assert_called_once()

    def test_schedule_status(self, web_server) -> None:
        server, client = web_server
        client.schedule_status.return_value = {"running": False}
        with urllib.request.urlopen(
            _url(server, "/api/schedule/status"), timeout=5
        ) as resp:
            data = json.loads(resp.read())
        assert data["running"] is False
        client.schedule_status.assert_called_once()


class TestSSE:
    def test_sse_content_type(self) -> None:
        client = MagicMock()
        client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 21.5,
            "setpoint": 20.0,
            "is_running": True,
        }
        server = JulaboWebServer(
            ("127.0.0.1", 0), client, sse_interval=0.01
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            sock.sendall(b"GET /api/events HTTP/1.1\r\nHost: localhost\r\n\r\n")
            # Read enough to get headers
            data = b""
            while b"\r\n\r\n" not in data:
                data += sock.recv(4096)
            headers = data.split(b"\r\n\r\n")[0].decode()
            assert "text/event-stream" in headers
            sock.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)

    def test_sse_first_event_valid_json(self) -> None:
        client = MagicMock()
        client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 21.5,
            "setpoint": 20.0,
            "is_running": True,
        }
        server = JulaboWebServer(
            ("127.0.0.1", 0), client, sse_interval=0.01
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            sock.sendall(b"GET /api/events HTTP/1.1\r\nHost: localhost\r\n\r\n")
            # Read enough data to get past headers + first event
            data = b""
            while b"data:" not in data:
                data += sock.recv(4096)
            # Parse the SSE event from the body
            body = data.split(b"\r\n\r\n", 1)[1]
            # Find the first "data: " line
            for line in body.split(b"\n"):
                if line.startswith(b"data: "):
                    payload = json.loads(line[6:])
                    assert payload["temperature"] == 21.5
                    break
            else:
                raise AssertionError("No data line found in SSE stream")
            sock.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)

    def test_sse_disconnect_handled(self) -> None:
        client = MagicMock()
        client.status_all.return_value = {
            "status": "01 OK",
            "temperature": 21.5,
            "setpoint": 20.0,
            "is_running": True,
        }
        server = JulaboWebServer(
            ("127.0.0.1", 0), client, sse_interval=0.01
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            sock.sendall(b"GET /api/events HTTP/1.1\r\nHost: localhost\r\n\r\n")
            # Read a bit then close abruptly
            sock.recv(1024)
            sock.close()
            # Server should not crash — verify it still serves
            import time
            time.sleep(0.1)
            url = f"http://127.0.0.1:{port}/api/status"
            with urllib.request.urlopen(url, timeout=5) as resp:
                assert resp.status == 200
        finally:
            server.shutdown()
            thread.join(timeout=5)


class TestHealthEndpoint:
    def test_health_returns_ok(self, web_server) -> None:
        server, _ = web_server
        with urllib.request.urlopen(_url(server, "/api/health"), timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"
        assert "version" in data

    def test_health_v1_path(self, web_server) -> None:
        server, _ = web_server
        with urllib.request.urlopen(_url(server, "/api/v1/health"), timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"

    def test_health_does_not_call_chiller(self, web_server) -> None:
        server, client = web_server
        client.status_all.reset_mock()
        with urllib.request.urlopen(_url(server, "/api/health"), timeout=5) as resp:
            json.loads(resp.read())
        client.status_all.assert_not_called()


class TestHistoryEndpoint:
    def test_history_endpoint_no_db_returns_503(self, web_server) -> None:
        server, _ = web_server
        try:
            urllib.request.urlopen(_url(server, "/api/history"), timeout=5)
            raise AssertionError("Should have raised")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503

    def test_history_endpoint_returns_data(self) -> None:
        from julabo_control.db import TemperatureDB

        client = MagicMock()
        db = TemperatureDB(":memory:")
        db.record(21.5, 20.0)
        server = JulaboWebServer(("127.0.0.1", 0), client, db=db)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/history?minutes=60", timeout=5
            ) as resp:
                data = json.loads(resp.read())
            assert len(data) == 1
            assert data[0]["temperature"] == 21.5
        finally:
            server.shutdown()
            thread.join(timeout=5)
            db.close()

    def test_history_endpoint_v1_path(self) -> None:
        from julabo_control.db import TemperatureDB

        client = MagicMock()
        db = TemperatureDB(":memory:")
        db.record(22.0, 20.0)
        server = JulaboWebServer(("127.0.0.1", 0), client, db=db)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/v1/history?minutes=60", timeout=5
            ) as resp:
                data = json.loads(resp.read())
            assert len(data) == 1
        finally:
            server.shutdown()
            thread.join(timeout=5)
            db.close()

    def test_history_invalid_minutes_400(self) -> None:
        from julabo_control.db import TemperatureDB

        client = MagicMock()
        db = TemperatureDB(":memory:")
        server = JulaboWebServer(("127.0.0.1", 0), client, db=db)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/history?minutes=abc", timeout=5
                )
                raise AssertionError("Should have raised")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
        finally:
            server.shutdown()
            thread.join(timeout=5)
            db.close()

    def test_history_zero_minutes_400(self) -> None:
        from julabo_control.db import TemperatureDB

        client = MagicMock()
        db = TemperatureDB(":memory:")
        server = JulaboWebServer(("127.0.0.1", 0), client, db=db)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/history?minutes=0", timeout=5
                )
                raise AssertionError("Should have raised")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
        finally:
            server.shutdown()
            thread.join(timeout=5)
            db.close()

    def test_history_too_large_minutes_400(self) -> None:
        from julabo_control.db import TemperatureDB

        client = MagicMock()
        db = TemperatureDB(":memory:")
        server = JulaboWebServer(("127.0.0.1", 0), client, db=db)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/history?minutes=999999", timeout=5
                )
                raise AssertionError("Should have raised")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
        finally:
            server.shutdown()
            thread.join(timeout=5)
            db.close()
