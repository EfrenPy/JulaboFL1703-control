"""Async TCP server for Julabo chiller control (asyncio-based)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

from .core import ChillerBackend, JulaboError
from .dispatch import _WRITE_COMMANDS, dispatch_command
from .remote_server import (
    MAX_MESSAGE_SIZE,
    PROTOCOL_VERSION,
    _RateLimiter,
)

LOGGER = logging.getLogger(__name__)


class AsyncJulaboServer:
    """Asyncio TCP server that proxies requests to a :class:`ChillerBackend`.

    Blocking serial calls are dispatched to a thread-pool executor so the
    event loop is never blocked.
    """

    def __init__(
        self,
        chiller: ChillerBackend,
        host: str = "127.0.0.1",
        port: int = 8765,
        auth_token: str | None = None,
        rate_limit: int = 0,
        read_only: bool = False,
    ) -> None:
        self._chiller = chiller
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._serial_lock = asyncio.Lock()
        self._rate_limiter: _RateLimiter | None = (
            _RateLimiter(max_requests=rate_limit) if rate_limit > 0 else None
        )
        self._read_only = read_only
        self._server: asyncio.AbstractServer | None = None

    # -- public interface ---------------------------------------------------

    async def start(self) -> None:
        """Start accepting connections."""
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port,
        )
        addrs = ", ".join(str(s.getsockname()) for s in self._server.sockets)
        LOGGER.info("Async server listening on %s", addrs)

    async def serve_forever(self) -> None:
        """Convenience wrapper: start + serve until cancelled."""
        await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    @property
    def port(self) -> int:
        """Return the bound port (useful when started with port=0)."""
        if self._server is not None:
            for sock in self._server.sockets:  # type: ignore[attr-defined]
                return sock.getsockname()[1]  # type: ignore[no-any-return]
        return self._port

    # -- connection handler -------------------------------------------------

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        client_ip = peer[0] if peer else "unknown"
        LOGGER.debug("Connection from %s", client_ip)
        try:
            while True:
                try:
                    raw = await reader.readuntil(b"\n")
                except asyncio.LimitOverrunError:
                    # Discard the oversized data and send error
                    buf = await reader.read(MAX_MESSAGE_SIZE + 1)  # noqa: F841
                    resp = {"status": "error", "error": "Message too large"}
                    writer.write(json.dumps(resp).encode() + b"\n")
                    await writer.drain()
                    continue
                except (asyncio.IncompleteReadError, ConnectionError):
                    break
                if len(raw) > MAX_MESSAGE_SIZE:
                    resp = {"status": "error", "error": "Message too large"}
                    writer.write(json.dumps(resp).encode() + b"\n")
                    await writer.drain()
                    continue
                line = raw.strip()
                if not line:
                    continue

                if (
                    self._rate_limiter is not None
                    and not self._rate_limiter.allow(client_ip)
                ):
                    resp = {"status": "error", "error": "Rate limit exceeded"}
                else:
                    try:
                        message = json.loads(line.decode())
                        resp = await self._process_command(message, client_ip)
                    except (
                        json.JSONDecodeError, ValueError, TypeError,
                        PermissionError, JulaboError, TimeoutError,
                    ) as exc:
                        resp = {"status": "error", "error": str(exc)}

                writer.write(json.dumps(resp).encode() + b"\n")
                await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    # -- command dispatch ---------------------------------------------------

    async def _process_command(
        self, message: dict[str, Any], client_ip: str = "",
    ) -> dict[str, Any]:
        if self._auth_token is not None:
            token = message.get("token")
            if token != self._auth_token:
                raise PermissionError("Invalid or missing authentication token")

        command = message.get("command")
        if not command:
            raise ValueError("Missing 'command' in request payload")

        if self._read_only and command in _WRITE_COMMANDS:
            return {"status": "error", "error": "Server is in read-only mode"}

        loop = asyncio.get_running_loop()
        async with self._serial_lock:
            result = await loop.run_in_executor(
                None, self._dispatch_command, command, message,
            )

        return {"status": "ok", "result": result, "protocol_version": PROTOCOL_VERSION}

    def _dispatch_command(
        self, command: str, message: dict[str, Any],
    ) -> Any:
        """Synchronous command dispatch — runs in the executor."""
        return dispatch_command(self._chiller, command, message)


def main() -> None:  # pragma: no cover - CLI helper
    """Run the async Julabo TCP server."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("serial_port", nargs="?", help="Serial port path")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--auth-token", default=None)
    parser.add_argument("--rate-limit", type=int, default=0)
    parser.add_argument("--read-only", action="store_true", default=False)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from .core import JulaboChiller, SerialSettings, auto_detect_port

    serial_port = args.serial_port or auto_detect_port(timeout=2.0)
    chiller = JulaboChiller(SerialSettings(port=serial_port))
    chiller.connect()

    server = AsyncJulaboServer(
        chiller, args.host, args.port,
        auth_token=args.auth_token,
        rate_limit=args.rate_limit,
        read_only=args.read_only,
    )

    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass
    finally:
        chiller.close()


if __name__ == "__main__":  # pragma: no cover
    main()
