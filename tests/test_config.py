"""Tests for julabo_control.config."""

from __future__ import annotations

import logging
from pathlib import Path

from julabo_control.config import get_bool, get_float, get_int, load_config


class TestLoadConfig:
    def test_missing_file(self, tmp_path: Path) -> None:
        result = load_config(tmp_path / "nonexistent.ini")
        assert result == {}

    def test_valid_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.ini"
        cfg.write_text(
            "[serial]\n"
            "port = /dev/ttyUSB0\n"
            "timeout = 3.0\n"
            "\n"
            "[gui]\n"
            "poll_interval = 10000\n"
            "alarm_threshold = 1.5\n"
            "\n"
            "[server]\n"
            "host = 0.0.0.0\n"
            "port = 9999\n"
            "auth_token = mysecret\n"
            "\n"
            "[remote]\n"
            "host = server.local\n"
            "port = 9999\n"
            "auth_token = mysecret\n",
            encoding="utf-8",
        )
        result = load_config(cfg)
        assert result["serial"]["port"] == "/dev/ttyUSB0"
        assert result["serial"]["timeout"] == "3.0"
        assert result["gui"]["poll_interval"] == "10000"
        assert result["gui"]["alarm_threshold"] == "1.5"
        assert result["server"]["host"] == "0.0.0.0"
        assert result["server"]["auth_token"] == "mysecret"
        assert result["remote"]["host"] == "server.local"

    def test_partial_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.ini"
        cfg.write_text("[serial]\nport = COM3\n", encoding="utf-8")
        result = load_config(cfg)
        assert "serial" in result
        assert result["serial"]["port"] == "COM3"
        assert "gui" not in result

    def test_type_conversions(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.ini"
        cfg.write_text(
            "[gui]\npoll_interval = 3000\nalarm_threshold = 0.5\n",
            encoding="utf-8",
        )
        result = load_config(cfg)
        assert int(result["gui"]["poll_interval"]) == 3000
        assert float(result["gui"]["alarm_threshold"]) == 0.5

    def test_default_path_used(self, monkeypatch, tmp_path: Path) -> None:
        cfg = tmp_path / ".julabo_control.ini"
        cfg.write_text("[serial]\nport = COM1\n", encoding="utf-8")
        monkeypatch.setattr("julabo_control.config.DEFAULT_CONFIG_PATH", cfg)
        result = load_config()
        assert result["serial"]["port"] == "COM1"


class TestConfigValidation:
    def test_unknown_section_warning(self, tmp_path: Path, caplog) -> None:
        cfg = tmp_path / "config.ini"
        cfg.write_text("[bogus]\nfoo = bar\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="julabo_control.config"):
            load_config(cfg)
        assert "Unknown config section: [bogus]" in caplog.text

    def test_unknown_key_warning(self, tmp_path: Path, caplog) -> None:
        cfg = tmp_path / "config.ini"
        cfg.write_text("[serial]\nport = COM1\nfoobar = baz\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="julabo_control.config"):
            load_config(cfg)
        assert "Unknown key 'foobar' in [serial]; valid keys:" in caplog.text

    def test_malformed_ini_returns_empty(self, tmp_path: Path, caplog) -> None:
        cfg = tmp_path / "bad.ini"
        cfg.write_text("[unclosed section\nkey = value\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="julabo_control.config"):
            result = load_config(cfg)
        assert result == {}
        assert "Failed to parse" in caplog.text

    def test_known_keys_no_warning(self, tmp_path: Path, caplog) -> None:
        cfg = tmp_path / "config.ini"
        cfg.write_text("[serial]\nport = COM1\ntimeout = 3.0\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="julabo_control.config"):
            load_config(cfg)
        assert "Unknown" not in caplog.text


class TestGetInt:
    def test_valid(self) -> None:
        assert get_int({"x": "42"}, "x", 0) == 42

    def test_missing_key(self) -> None:
        assert get_int({}, "x", 99) == 99

    def test_invalid_value(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="julabo_control.config"):
            result = get_int({"x": "abc"}, "x", 10)
        assert result == 10
        assert "Invalid integer" in caplog.text

    def test_clamped_min(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="julabo_control.config"):
            result = get_int({"x": "5"}, "x", 10, min_val=10)
        assert result == 10
        assert "clamped to minimum" in caplog.text

    def test_clamped_max(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="julabo_control.config"):
            result = get_int({"x": "200"}, "x", 10, max_val=100)
        assert result == 100
        assert "clamped to maximum" in caplog.text


class TestGetFloat:
    def test_valid(self) -> None:
        assert get_float({"x": "3.14"}, "x", 0.0) == 3.14

    def test_missing_key(self) -> None:
        assert get_float({}, "x", 1.5) == 1.5

    def test_invalid_value(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="julabo_control.config"):
            result = get_float({"x": "abc"}, "x", 2.5)
        assert result == 2.5
        assert "Invalid float" in caplog.text

    def test_clamped_min(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="julabo_control.config"):
            result = get_float({"x": "0.1"}, "x", 1.0, min_val=1.0)
        assert result == 1.0

    def test_clamped_max(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="julabo_control.config"):
            result = get_float({"x": "99.0"}, "x", 1.0, max_val=10.0)
        assert result == 10.0


class TestGetBool:
    def test_true_values(self) -> None:
        for val in ("1", "true", "yes", "on", "True", "YES"):
            assert get_bool({"x": val}, "x", False) is True

    def test_false_values(self) -> None:
        for val in ("0", "false", "no", "off", "False", "NO"):
            assert get_bool({"x": val}, "x", True) is False

    def test_missing_key(self) -> None:
        assert get_bool({}, "x", True) is True
        assert get_bool({}, "x", False) is False

    def test_invalid_value(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="julabo_control.config"):
            result = get_bool({"x": "maybe"}, "x", False)
        assert result is False
        assert "Invalid boolean" in caplog.text
