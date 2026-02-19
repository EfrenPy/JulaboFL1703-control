"""Configuration file support for Julabo applications."""

from __future__ import annotations

import configparser
import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".julabo_control.ini"

KNOWN_SECTIONS: dict[str, set[str]] = {
    "serial": {"port", "baudrate", "timeout"},
    "gui": {
        "poll_interval",
        "alarm_threshold",
        "alarm_log",
        "temperature_log",
        "desktop_notifications",
        "font_size",
    },
    "server": {
        "host",
        "port",
        "baudrate",
        "timeout",
        "serial_port",
        "auth_token",
        "tls_cert",
        "tls_key",
        "rate_limit",
        "log_traffic",
        "read_only",
        "idle_timeout",
        "audit_log",
        "watchdog",
        "metrics_port",
        "log_format",
        "alertmanager_url",
        "alertmanager_chiller_id",
    },
    "remote": {
        "host",
        "port",
        "timeout",
        "poll_interval",
        "auth_token",
        "tls",
        "tls_ca",
        "alarm_log",
        "temperature_log",
        "desktop_notifications",
        "log_traffic",
    },
    "web": {
        "host",
        "port",
        "web_host",
        "web_port",
        "auth_token",
        "db_path",
    },
    "mqtt": {
        "broker",
        "port",
        "topic_prefix",
        "publish_interval",
        "username",
        "password",
        "host",
        "server_port",
        "auth_token",
    },
}


def _validate_config(config: dict[str, dict[str, str]]) -> None:
    """Log warnings for unknown sections and keys."""
    for section, keys in config.items():
        if section not in KNOWN_SECTIONS:
            LOGGER.warning("Unknown config section: [%s]", section)
            continue
        known_keys = KNOWN_SECTIONS[section]
        for key in keys:
            if key not in known_keys:
                LOGGER.warning(
                    "Unknown key '%s' in [%s]; valid keys: %s",
                    key,
                    section,
                    ", ".join(sorted(known_keys)),
                )


def get_int(
    config: dict[str, str],
    key: str,
    default: int,
    *,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """Read an integer from a config dict with range clamping."""
    raw = config.get(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("Invalid integer for '%s': %r, using default %d", key, raw, default)
        return default
    if min_val is not None and value < min_val:
        LOGGER.warning("Value for '%s' clamped to minimum %d", key, min_val)
        return min_val
    if max_val is not None and value > max_val:
        LOGGER.warning("Value for '%s' clamped to maximum %d", key, max_val)
        return max_val
    return value


def get_float(
    config: dict[str, str],
    key: str,
    default: float,
    *,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """Read a float from a config dict with range clamping."""
    raw = config.get(key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        LOGGER.warning("Invalid float for '%s': %r, using default %s", key, raw, default)
        return default
    if min_val is not None and value < min_val:
        LOGGER.warning("Value for '%s' clamped to minimum %s", key, min_val)
        return min_val
    if max_val is not None and value > max_val:
        LOGGER.warning("Value for '%s' clamped to maximum %s", key, max_val)
        return max_val
    return value


def get_bool(config: dict[str, str], key: str, default: bool) -> bool:
    """Read a boolean from a config dict."""
    raw = config.get(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    LOGGER.warning("Invalid boolean for '%s': %r, using default %s", key, raw, default)
    return default


def load_config(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Load an INI configuration file and return its contents as nested dicts.

    Returns an empty dict when the file does not exist or cannot be parsed.
    Supported sections: ``[serial]``, ``[gui]``, ``[server]``, ``[remote]``, ``[web]``, ``[mqtt]``.
    """
    if path is None:
        path = DEFAULT_CONFIG_PATH

    if not path.exists():
        LOGGER.debug("Config file not found: %s", path)
        return {}

    parser = configparser.ConfigParser()
    try:
        parser.read(str(path), encoding="utf-8")
    except configparser.Error as exc:
        LOGGER.warning("Failed to parse config file %s: %s", path, exc)
        return {}

    result: dict[str, dict[str, str]] = {}
    for section in parser.sections():
        result[section] = dict(parser[section])
    LOGGER.debug("Loaded config from %s: sections=%s", path, list(result.keys()))
    _validate_config(result)
    return result
