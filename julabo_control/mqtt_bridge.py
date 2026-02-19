"""MQTT bridge for publishing Julabo chiller status and receiving commands."""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from typing import Any

LOGGER = logging.getLogger(__name__)


class MQTTBridge:
    """Publishes chiller status over MQTT and dispatches incoming commands.

    Requires the ``paho-mqtt`` package (install via ``pip install julabo-control[mqtt]``).
    """

    def __init__(
        self,
        client: Any,
        broker: str,
        port: int = 1883,
        topic_prefix: str = "julabo",
        publish_interval: float = 5.0,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        import paho.mqtt.client as mqtt_mod

        self._chiller_client = client
        self.broker = broker
        self.port = port
        self.topic_prefix = topic_prefix
        self.publish_interval = publish_interval

        self._mqtt = mqtt_mod.Client()
        if username:
            self._mqtt.username_pw_set(username, password)
        self._mqtt.on_message = self._on_message
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Connect to the MQTT broker and start the publish loop."""
        self._mqtt.connect(self.broker, self.port)
        self._mqtt.subscribe(f"{self.topic_prefix}/command/#")
        self._mqtt.loop_start()
        self._thread = threading.Thread(target=self._publish_loop, daemon=True)
        self._thread.start()
        LOGGER.info(
            "MQTT bridge started: %s:%d prefix=%s",
            self.broker, self.port, self.topic_prefix,
        )

    def stop(self) -> None:
        """Disconnect and stop all threads."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._mqtt.loop_stop()
        self._mqtt.disconnect()
        LOGGER.info("MQTT bridge stopped")

    def _publish_loop(self) -> None:
        while not self._stop_event.wait(self.publish_interval):
            try:
                data = self._chiller_client.status_all()
                payload = json.dumps(data)
                self._mqtt.publish(f"{self.topic_prefix}/status", payload)
            except Exception as exc:
                LOGGER.error("MQTT publish error: %s", exc)

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        topic = message.topic
        try:
            if topic == f"{self.topic_prefix}/command/setpoint":
                value = float(message.payload.decode())
                self._chiller_client.command("set_setpoint", value)
                LOGGER.info("MQTT: set_setpoint %.2f", value)
            elif topic == f"{self.topic_prefix}/command/start":
                self._chiller_client.command("start")
                LOGGER.info("MQTT: start")
            elif topic == f"{self.topic_prefix}/command/stop":
                self._chiller_client.command("stop")
                LOGGER.info("MQTT: stop")
            else:
                LOGGER.warning("MQTT: unknown command topic: %s", topic)
        except Exception as exc:
            LOGGER.error("MQTT command error on %s: %s", topic, exc)


def main() -> None:  # pragma: no cover - CLI helper
    """Run the Julabo MQTT bridge."""
    from .config import load_config
    from .remote_client import RemoteChillerClient

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker", default=None, help="MQTT broker hostname")
    parser.add_argument(
        "--mqtt-port", type=int, default=None, help="MQTT broker port (default: 1883)",
    )
    parser.add_argument("--topic-prefix", default=None, help="MQTT topic prefix (default: julabo)")
    parser.add_argument(
        "--publish-interval", type=float, default=None,
        help="Seconds between status publishes (default: 5)",
    )
    parser.add_argument("--mqtt-username", default=None, help="MQTT username")
    parser.add_argument("--mqtt-password", default=None, help="MQTT password")
    parser.add_argument("--host", default=None, help="TCP server host (default: localhost)")
    parser.add_argument("--port", type=int, default=None, help="TCP server port (default: 8765)")
    parser.add_argument("--auth-token", default=None, help="Auth token for the TCP server")
    parser.add_argument("--config", default=None, help="Path to configuration file")
    args = parser.parse_args()

    from pathlib import Path

    config_path = Path(args.config) if args.config else None
    config = load_config(config_path)
    mqtt_cfg = config.get("mqtt", {})

    broker = args.broker or mqtt_cfg.get("broker", "localhost")
    mqtt_port = args.mqtt_port if args.mqtt_port is not None else int(mqtt_cfg.get("port", "1883"))
    topic_prefix = args.topic_prefix or mqtt_cfg.get("topic_prefix", "julabo")
    publish_interval = (
        args.publish_interval if args.publish_interval is not None
        else float(mqtt_cfg.get("publish_interval", "5"))
    )
    username = args.mqtt_username or mqtt_cfg.get("username")
    password = args.mqtt_password or mqtt_cfg.get("password")

    host = args.host or mqtt_cfg.get("host", "localhost")
    port = args.port if args.port is not None else int(mqtt_cfg.get("server_port", "8765"))
    auth_token = args.auth_token or mqtt_cfg.get("auth_token")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    client = RemoteChillerClient(host, port, auth_token=auth_token)
    bridge = MQTTBridge(
        client, broker, port=mqtt_port, topic_prefix=topic_prefix,
        publish_interval=publish_interval, username=username, password=password,
    )
    bridge.start()
    LOGGER.info("MQTT bridge running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
