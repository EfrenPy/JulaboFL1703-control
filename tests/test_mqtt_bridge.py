"""Tests for julabo_control.mqtt_bridge — all paho.mqtt is mocked."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


# Create a mock paho.mqtt module so tests run without it installed
@pytest.fixture(autouse=True)
def mock_paho(monkeypatch):
    """Inject a mock paho.mqtt.client module."""
    mock_mqtt_mod = MagicMock()
    mock_client_class = MagicMock()
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance
    mock_mqtt_mod.Client = mock_client_class

    paho_pkg = types.ModuleType("paho")
    paho_mqtt = types.ModuleType("paho.mqtt")
    paho_mqtt_client = mock_mqtt_mod

    monkeypatch.setitem(sys.modules, "paho", paho_pkg)
    monkeypatch.setitem(sys.modules, "paho.mqtt", paho_mqtt)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", paho_mqtt_client)

    yield mock_client_instance


from julabo_control.mqtt_bridge import MQTTBridge  # noqa: E402


class TestMQTTBridge:
    def test_bridge_init(self, mock_paho) -> None:
        client = MagicMock()
        bridge = MQTTBridge(client, "mqtt.local", port=1883, topic_prefix="test")
        assert bridge.broker == "mqtt.local"
        assert bridge.port == 1883
        assert bridge.topic_prefix == "test"

    def test_publish_cycle(self, mock_paho) -> None:
        client = MagicMock()
        client.status_all.return_value = {"temperature": 21.5}
        bridge = MQTTBridge(
            client, "mqtt.local", publish_interval=0.01
        )
        bridge.start()
        import time
        time.sleep(0.1)
        bridge.stop()
        mock_paho.publish.assert_called()
        # Verify topic
        call_args = mock_paho.publish.call_args
        assert "julabo/status" in call_args[0][0]

    def test_command_setpoint(self, mock_paho) -> None:
        client = MagicMock()
        bridge = MQTTBridge(client, "mqtt.local", topic_prefix="julabo")
        msg = MagicMock()
        msg.topic = "julabo/command/setpoint"
        msg.payload = b"25.0"
        bridge._on_message(None, None, msg)
        client.command.assert_called_once_with("set_setpoint", 25.0)

    def test_command_start(self, mock_paho) -> None:
        client = MagicMock()
        bridge = MQTTBridge(client, "mqtt.local", topic_prefix="julabo")
        msg = MagicMock()
        msg.topic = "julabo/command/start"
        msg.payload = b""
        bridge._on_message(None, None, msg)
        client.command.assert_called_once_with("start")

    def test_command_stop(self, mock_paho) -> None:
        client = MagicMock()
        bridge = MQTTBridge(client, "mqtt.local", topic_prefix="julabo")
        msg = MagicMock()
        msg.topic = "julabo/command/stop"
        msg.payload = b""
        bridge._on_message(None, None, msg)
        client.command.assert_called_once_with("stop")

    def test_publish_error_recovery(self, mock_paho) -> None:
        client = MagicMock()
        client.status_all.side_effect = RuntimeError("no conn")
        bridge = MQTTBridge(
            client, "mqtt.local", publish_interval=0.01
        )
        bridge.start()
        import time
        time.sleep(0.1)
        bridge.stop()
        # Should not crash

    def test_stop_graceful(self, mock_paho) -> None:
        client = MagicMock()
        client.status_all.return_value = {"temperature": 21.5}
        bridge = MQTTBridge(
            client, "mqtt.local", publish_interval=0.01
        )
        bridge.start()
        bridge.stop()
        mock_paho.loop_stop.assert_called_once()
        mock_paho.disconnect.assert_called_once()

    def test_main_args(self, mock_paho) -> None:
        """Verify main() can be imported — actual execution in pragma:no cover."""
        from julabo_control.mqtt_bridge import main
        assert callable(main)

    def test_unknown_topic_logged_as_warning(self, mock_paho, caplog) -> None:
        import logging

        client = MagicMock()
        bridge = MQTTBridge(client, "mqtt.local", topic_prefix="julabo")
        msg = MagicMock()
        msg.topic = "julabo/command/unknown_action"
        msg.payload = b""
        with caplog.at_level(logging.WARNING, logger="julabo_control.mqtt_bridge"):
            bridge._on_message(None, None, msg)
        assert "unknown command topic" in caplog.text

    def test_publish_loop_continues_after_error(self, mock_paho) -> None:
        """Publish loop should continue after a status_all error."""
        client = MagicMock()
        call_count = 0

        def status_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("temporary failure")
            return {"temperature": 21.5}

        client.status_all.side_effect = status_side_effect
        bridge = MQTTBridge(client, "mqtt.local", publish_interval=0.01)
        bridge.start()
        import time
        time.sleep(0.2)
        bridge.stop()
        # Should have been called multiple times despite errors
        assert call_count >= 3

    def test_invalid_setpoint_payload_caught(self, mock_paho, caplog) -> None:
        import logging

        client = MagicMock()
        bridge = MQTTBridge(client, "mqtt.local", topic_prefix="julabo")
        msg = MagicMock()
        msg.topic = "julabo/command/setpoint"
        msg.payload = b"not-a-number"
        with caplog.at_level(logging.ERROR, logger="julabo_control.mqtt_bridge"):
            bridge._on_message(None, None, msg)
        assert "command error" in caplog.text.lower()
        client.command.assert_not_called()
