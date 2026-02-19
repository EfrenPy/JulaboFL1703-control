"""Shared command dispatcher for Julabo TCP servers (sync and async)."""

from __future__ import annotations

from typing import Any

from .core import ChillerBackend

_WRITE_COMMANDS = frozenset({"set_setpoint", "start", "stop", "set_running"})


def _normalize_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "on", "start", "run"}:
            return True
        if normalized in {"0", "false", "off", "stop", "halt"}:
            return False
    raise ValueError(
        "Unable to interpret 'value' for set_running. Use a boolean, number, or supported string."
    )


def dispatch_command(
    chiller: ChillerBackend, command: str, message: dict[str, Any],
) -> Any:
    """Execute a core chiller command and return the result.

    Handles the 11 standard commands: identify, status, get_setpoint,
    set_setpoint, temperature, is_running, start, stop, set_running,
    status_all, and ping.

    Raises :class:`ValueError` for unknown commands or missing parameters.
    """
    if command == "identify":
        return chiller.identify()
    elif command == "status":
        return chiller.get_status()
    elif command == "get_setpoint":
        return chiller.get_setpoint()
    elif command == "set_setpoint":
        value = message.get("value")
        if value is None:
            raise ValueError("'set_setpoint' requires a numeric 'value'")
        chiller.set_setpoint(float(value))
        return chiller.get_setpoint()
    elif command == "temperature":
        return chiller.get_temperature()
    elif command == "is_running":
        return chiller.is_running()
    elif command == "start":
        return chiller.start()
    elif command == "stop":
        return chiller.stop()
    elif command == "set_running":
        target = message.get("value")
        if target is None:
            raise ValueError("'set_running' requires a boolean 'value'")
        return chiller.set_running(_normalize_boolean(target))
    elif command == "status_all":
        return {
            "status": chiller.get_status(),
            "temperature": chiller.get_temperature(),
            "setpoint": chiller.get_setpoint(),
            "is_running": chiller.is_running(),
        }
    elif command == "ping":
        return "pong"
    else:
        raise ValueError(f"Unsupported command: {command}")
