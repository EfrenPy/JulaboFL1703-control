"""Browser-based dashboard for remote Julabo control."""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from . import __version__
from .core import SETPOINT_MAX, SETPOINT_MIN

LOGGER = logging.getLogger(__name__)

_SAFE_ERROR_TYPES = (ValueError, TypeError, KeyError)


def _sanitize_web_error(exc: Exception) -> str:
    """Return a safe error message, hiding internal details for unknown types."""
    if isinstance(exc, _SAFE_ERROR_TYPES):
        return str(exc)
    return "Internal server error"

_HTML_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Julabo Control</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#f5f5f5;color:#333;padding:20px}
.container{max-width:900px;margin:0 auto}
h1{margin-bottom:20px;color:#1a1a2e}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:20px}
.card{background:#fff;border-radius:8px;padding:16px;box-shadow:0 2px 4px rgba(0,0,0,0.1)}
.card-label{font-size:0.85em;color:#666;margin-bottom:4px}
.card-value{font-size:1.5em;font-weight:600}
.running{color:#2ecc71}.stopped{color:#e74c3c}
.controls{background:#fff;border-radius:8px;padding:16px;
box-shadow:0 2px 4px rgba(0,0,0,0.1);margin-bottom:20px;
display:flex;gap:12px;align-items:center;flex-wrap:wrap}
input[type=number]{width:80px;padding:6px;border:1px solid #ccc;border-radius:4px}
button{padding:8px 16px;border:none;border-radius:4px;cursor:pointer;font-size:0.9em}
.btn-apply{background:#3498db;color:#fff}
.btn-start{background:#2ecc71;color:#fff}
.btn-stop{background:#e74c3c;color:#fff}
.chart-box{background:#fff;border-radius:8px;padding:16px;box-shadow:0 2px 4px rgba(0,0,0,0.1)}
.status-msg{margin-top:8px;font-size:0.85em;color:#666}
</style>
</head>
<body>
<div class="container">
<h1>Julabo Chiller Control</h1>
<div class="cards">
<div class="card"><div class="card-label">Temperature</div>
<div class="card-value" id="temp">--</div></div>
<div class="card"><div class="card-label">Setpoint</div>
<div class="card-value" id="sp">--</div></div>
<div class="card"><div class="card-label">Status</div>
<div class="card-value" id="status">--</div></div>
<div class="card"><div class="card-label">Machine</div>
<div class="card-value" id="running">--</div></div>
</div>
<div class="controls">
<label>Setpoint: <input type="number" id="newSp" step="0.1"></label>
<button class="btn-apply" onclick="applySp()">Apply</button>
<button class="btn-start" onclick="doStart()">Start</button>
<button class="btn-stop" onclick="doStop()">Stop</button>
</div>
<div class="controls">
<label>Schedule CSV:</label>
<textarea id="schedCsv" rows="3" cols="40"
placeholder="elapsed_minutes,temperature_c&#10;0,20&#10;10,30"></textarea>
<button class="btn-apply" onclick="uploadSchedule()">Upload</button>
<button class="btn-stop" onclick="stopSchedule()">Stop Schedule</button>
<span id="schedStatus" style="font-size:0.85em;color:#666"></span>
</div>
<div class="chart-box"><canvas id="chart"></canvas></div>
<div class="status-msg" id="msg"></div>
</div>
<script>
const maxPts=120,temps=[],labels=[];
const ctx=document.getElementById('chart').getContext('2d');
const chart=new Chart(ctx,{type:'line',data:{labels:labels,datasets:[
{label:'Temperature',data:temps,borderColor:'#3498db',tension:0.3,pointRadius:2}
]},options:{responsive:true,scales:{x:{title:{display:true,text:'Reading'}},
y:{title:{display:true,text:'\\u00b0C'}}},animation:false}});
function msg(t){document.getElementById('msg').textContent=t}
function updateUI(d){
document.getElementById('temp').textContent=d.temperature.toFixed(2)+'\\u00b0C';
document.getElementById('sp').textContent=d.setpoint.toFixed(2)+'\\u00b0C';
document.getElementById('status').textContent=d.status;
const el=document.getElementById('running');
el.textContent=d.is_running?'Running':'Stopped';
el.className='card-value '+(d.is_running?'running':'stopped');
temps.push(d.temperature);labels.push(temps.length);
if(temps.length>maxPts){temps.shift();labels.shift()}
chart.update();msg('')}
async function refresh(){
try{const r=await fetch('/api/status');const d=await r.json();
updateUI(d)}catch(e){msg('Error: '+e)}
}
let pollId=null;
function startPolling(){pollId=setInterval(refresh,5000)}
function stopPolling(){if(pollId){clearInterval(pollId);pollId=null}}
try{const es=new EventSource('/api/events');
es.onmessage=function(e){try{updateUI(JSON.parse(e.data))}catch(err){msg('SSE parse error')}};
es.onerror=function(){es.close();startPolling()}}catch(e){startPolling()}
async function applySp(){const v=document.getElementById('newSp').value;
if(!v){msg('Enter a value');return}
try{await fetch('/api/setpoint',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({value:parseFloat(v)})});
msg('Setpoint updated');refresh()}catch(e){msg('Error: '+e)}}
async function doStart(){try{await fetch('/api/start',
{method:'POST'});msg('Started');refresh()
}catch(e){msg('Error: '+e)}}
async function doStop(){try{await fetch('/api/stop',
{method:'POST'});msg('Stopped');refresh()
}catch(e){msg('Error: '+e)}}
async function uploadSchedule(){const csv=document.getElementById('schedCsv').value;
if(!csv){msg('Enter CSV data');return}
try{const r=await fetch('/api/schedule',{method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify({csv:csv})});const d=await r.json();
if(d.status==='ok'){msg('Schedule uploaded');refreshScheduleStatus()}
else{msg('Error: '+(d.error||'unknown'))}}catch(e){msg('Error: '+e)}}
async function stopSchedule(){try{await fetch('/api/schedule',
{method:'DELETE'});msg('Schedule stopped');
document.getElementById('schedStatus').textContent='Stopped'}catch(e){msg('Error: '+e)}}
async function refreshScheduleStatus(){try{const r=await fetch('/api/schedule/status');
const d=await r.json();const el=document.getElementById('schedStatus');
if(d.running){el.textContent='Running: '+d.elapsed_minutes+'/'+d.total_minutes+' min'}
else{el.textContent='Not running'}}catch(e){}}
refresh();setInterval(refreshScheduleStatus,5000);
</script>
</body>
</html>
"""


_OPENAPI_SPEC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Julabo Control API", "version": __version__},
    "paths": {
        "/api/v1/status": {
            "get": {"summary": "Get chiller status", "responses": {"200": {"description": "OK"}}}
        },
        "/api/v1/setpoint": {
            "post": {
                "summary": "Set the temperature setpoint",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "value": {
                                        "type": "number",
                                        "minimum": SETPOINT_MIN,
                                        "maximum": SETPOINT_MAX,
                                    }
                                },
                                "required": ["value"],
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad request"}},
            }
        },
        "/api/v1/start": {
            "post": {"summary": "Start circulation", "responses": {"200": {"description": "OK"}}}
        },
        "/api/v1/stop": {
            "post": {"summary": "Stop circulation", "responses": {"200": {"description": "OK"}}}
        },
        "/api/v1/events": {
            "get": {"summary": "SSE event stream", "responses": {"200": {"description": "OK"}}}
        },
        "/api/v1/schedule": {
            "post": {"summary": "Upload schedule CSV", "responses": {"200": {"description": "OK"}}},
            "delete": {"summary": "Stop schedule", "responses": {"200": {"description": "OK"}}},
        },
        "/api/v1/schedule/status": {
            "get": {"summary": "Get schedule status", "responses": {"200": {"description": "OK"}}}
        },
        "/api/v1/health": {
            "get": {
                "summary": "Health check (no chiller query)",
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/api/v1/history": {
            "get": {
                "summary": "Query temperature history",
                "parameters": [
                    {"name": "minutes", "in": "query", "schema": {"type": "integer", "default": 60}}
                ],
                "responses": {
                    "200": {"description": "OK"},
                    "503": {"description": "No DB configured"},
                },
            }
        },
    },
}


class JulaboWebHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Julabo web dashboard."""

    server: JulaboWebServer  # type: ignore[assignment]

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Strip /api/v1/ prefix to canonical /api/ form."""
        if path.startswith("/api/v1/"):
            return "/api/" + path[len("/api/v1/"):]
        return path

    def do_GET(self) -> None:
        path = self._normalize_path(self.path.split("?")[0])
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        if path == "/":
            self._html_response(200, _HTML_PAGE)
        elif path == "/api/status":
            try:
                data = self.server.client.status_all()
                self._json_response(200, data)
            except Exception as exc:
                self._json_response(500, {"error": _sanitize_web_error(exc)})
        elif path == "/api/events":
            self._handle_sse()
        elif path == "/api/schedule/status":
            try:
                data = self.server.client.schedule_status()
                self._json_response(200, data)
            except Exception as exc:
                self._json_response(500, {"error": _sanitize_web_error(exc)})
        elif path == "/api/openapi.json":
            self._json_response(200, _OPENAPI_SPEC)
        elif path == "/api/history":
            self._handle_history(query)
        elif path == "/api/health":
            self._json_response(200, {"status": "ok", "version": __version__})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = self._normalize_path(self.path)
        if path == "/api/setpoint":
            try:
                body = self._read_json_body()
                if body is None:
                    return
                value = body.get("value")
                if value is None:
                    self._json_response(400, {"error": "Missing 'value'"})
                    return
                try:
                    fvalue = float(value)
                except (TypeError, ValueError):
                    self._json_response(
                        400, {"error": "Invalid numeric value for 'value'"}
                    )
                    return
                if not (SETPOINT_MIN <= fvalue <= SETPOINT_MAX):
                    self._json_response(
                        400,
                        {
                            "error": f"Setpoint must be between "
                            f"{SETPOINT_MIN} and {SETPOINT_MAX}"
                        },
                    )
                    return
                self.server.client.command("set_setpoint", fvalue)
                self._json_response(200, {"status": "ok"})
            except Exception as exc:
                self._json_response(500, {"error": _sanitize_web_error(exc)})
        elif path == "/api/start":
            try:
                self.server.client.command("start")
                self._json_response(200, {"status": "ok"})
            except Exception as exc:
                self._json_response(500, {"error": _sanitize_web_error(exc)})
        elif path == "/api/stop":
            try:
                self.server.client.command("stop")
                self._json_response(200, {"status": "ok"})
            except Exception as exc:
                self._json_response(500, {"error": _sanitize_web_error(exc)})
        elif path == "/api/schedule":
            try:
                body = self._read_json_body()
                if body is None:
                    return
                csv_data = body.get("csv")
                if not csv_data:
                    self._json_response(400, {"error": "Missing 'csv'"})
                    return
                result = self.server.client.load_schedule(csv_data)
                self._json_response(200, {"status": "ok", "result": result})
            except Exception as exc:
                self._json_response(500, {"error": _sanitize_web_error(exc)})
        else:
            self.send_error(404)

    def do_DELETE(self) -> None:
        path = self._normalize_path(self.path)
        if path == "/api/schedule":
            try:
                result = self.server.client.stop_schedule()
                self._json_response(200, {"status": "ok", "result": result})
            except Exception as exc:
                self._json_response(500, {"error": _sanitize_web_error(exc)})
        else:
            self.send_error(404)

    def _handle_history(self, query: str) -> None:
        """Serve temperature history from the SQLite DB."""
        db = self.server.db
        if db is None:
            self._json_response(503, {"error": "Database not configured"})
            return
        import urllib.parse

        params = urllib.parse.parse_qs(query)
        raw_minutes = params.get("minutes", ["60"])[0]
        try:
            minutes = int(raw_minutes)
        except ValueError:
            self._json_response(400, {"error": "Invalid 'minutes' parameter"})
            return
        if not (1 <= minutes <= 525600):
            self._json_response(
                400,
                {"error": "Parameter 'minutes' must be between 1 and 525600"},
            )
            return
        rows = db.query_recent(minutes)
        self._json_response(200, rows)

    def _handle_sse(self) -> None:
        """Stream Server-Sent Events with periodic status updates."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    data = self.server.client.status_all()
                except Exception as exc:
                    data = {"error": _sanitize_web_error(exc)}
                payload = f"data: {json.dumps(data)}\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
                time.sleep(self.server.sse_interval)
        except (BrokenPipeError, ConnectionError, OSError):
            pass

    def _json_response(self, code: int, data: Any) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, code: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            raw = self.rfile.read(length)
            try:
                return json.loads(raw)  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                self._json_response(400, {"error": "Invalid JSON"})
                return None
        return {}

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.debug(fmt, *args)


class JulaboWebServer(HTTPServer):
    """HTTP server that proxies to a RemoteChillerClient."""

    def __init__(
        self,
        server_address: tuple[str, int],
        client: Any,
        *,
        sse_interval: float = 5.0,
        db: Any = None,
        ws_port: int | None = None,
    ) -> None:
        super().__init__(server_address, JulaboWebHandler)
        self.client = client
        self.sse_interval = sse_interval
        self.db = db
        self._ws_thread: threading.Thread | None = None
        self._ws_loop: Any = None
        if ws_port is not None:
            self._start_ws(ws_port)

    def shutdown(self) -> None:
        """Shut down both HTTP and WebSocket servers."""
        ws_server = getattr(self, "_ws_server", None)
        loop = self._ws_loop
        if ws_server is not None and loop is not None and loop.is_running():
            loop.call_soon_threadsafe(ws_server.close)
            loop.call_soon_threadsafe(loop.stop)
        super().shutdown()
        if self._ws_thread is not None:
            self._ws_thread.join(timeout=2.0)

    def _start_ws(self, port: int) -> None:
        """Start a WebSocket server in a background thread if websockets is installed."""
        try:
            import asyncio

            import websockets  # type: ignore[import-untyped]
        except ImportError:
            LOGGER.warning("websockets not installed, WebSocket support disabled")
            return

        server_ref = self

        async def _ws_handler(ws: Any) -> None:
            await ws.send(json.dumps({"type": "connected"}))
            while True:
                try:
                    try:
                        msg_raw = await asyncio.wait_for(
                            ws.recv(), timeout=server_ref.sse_interval
                        )
                    except asyncio.TimeoutError:
                        msg_raw = None

                    if msg_raw is not None:
                        try:
                            msg = json.loads(msg_raw)
                            cmd = msg.get("command")
                            if cmd == "set_setpoint":
                                server_ref.client.command(
                                    "set_setpoint", float(msg["value"])
                                )
                                await ws.send(
                                    json.dumps({"type": "ack", "command": cmd})
                                )
                            elif cmd in ("start", "stop"):
                                server_ref.client.command(cmd)
                                await ws.send(
                                    json.dumps({"type": "ack", "command": cmd})
                                )
                            else:
                                await ws.send(
                                    json.dumps(
                                        {
                                            "type": "error",
                                            "error": f"Unknown command: {cmd}",
                                        }
                                    )
                                )
                        except (
                            json.JSONDecodeError,
                            KeyError,
                            TypeError,
                            ValueError,
                        ) as exc:
                            await ws.send(
                                json.dumps({"type": "error", "error": str(exc)})
                            )
                    else:
                        try:
                            data = server_ref.client.status_all()
                            await ws.send(
                                json.dumps({"type": "status", **data})
                            )
                        except Exception as exc:
                            await ws.send(
                                json.dumps({"type": "error", "error": str(exc)})
                            )
                except websockets.ConnectionClosed:
                    break
                except Exception:
                    break

        server_self = self
        self._ws_server: Any = None

        async def _run_ws_managed() -> None:
            ws_server = await websockets.serve(  # type: ignore[attr-defined]
                _ws_handler, "0.0.0.0", port,
            )
            server_self._ws_server = ws_server
            LOGGER.info("WebSocket server on ws://0.0.0.0:%d", port)
            await asyncio.Future()  # run forever

        def _thread_target() -> None:
            loop = asyncio.new_event_loop()
            server_self._ws_loop = loop
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_run_ws_managed())
            except RuntimeError:
                pass
            finally:
                loop.close()

        self._ws_thread = threading.Thread(target=_thread_target, daemon=True)
        self._ws_thread.start()


def main() -> None:  # pragma: no cover - CLI helper
    """Run the Julabo web dashboard."""
    from .config import load_config
    from .remote_client import RemoteChillerClient

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", default=None,
        help="TCP server host (default: localhost)",
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help="TCP server port (default: 8765)",
    )
    parser.add_argument(
        "--web-host", default=None,
        help="Web server bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--web-port", type=int, default=None,
        help="Web server port (default: 8080)",
    )
    parser.add_argument(
        "--auth-token", default=None,
        help="Auth token for the TCP server",
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to configuration file",
    )
    args = parser.parse_args()

    from pathlib import Path

    config_path = Path(args.config) if args.config else None
    config = load_config(config_path)
    web_cfg = config.get("web", {})

    host = args.host or web_cfg.get("host", "localhost")
    port = args.port if args.port is not None else int(web_cfg.get("port", "8765"))
    web_host = args.web_host or web_cfg.get("web_host", "0.0.0.0")
    web_port = (
        args.web_port if args.web_port is not None
        else int(web_cfg.get("web_port", "8080"))
    )
    auth_token = args.auth_token or web_cfg.get("auth_token")

    client = RemoteChillerClient(host, port, auth_token=auth_token)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    server = JulaboWebServer((web_host, web_port), client)
    LOGGER.info("Web UI serving on http://%s:%d", web_host, web_port)
    LOGGER.info("Proxying to TCP server at %s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":  # pragma: no cover
    main()
