"""Temperature alarm support for Julabo applications."""

from __future__ import annotations

import csv
import json
import logging
import urllib.request
from datetime import datetime, timezone
from typing import IO, Any, Callable

LOGGER = logging.getLogger(__name__)


class AlertmanagerClient:
    """Sends alerts to Prometheus Alertmanager via its HTTP API."""

    def __init__(
        self,
        url: str,
        chiller_id: str = "default",
    ) -> None:
        self._url = url.rstrip("/")
        self._chiller_id = chiller_id

    def send_firing(
        self, temperature: float, setpoint: float, threshold: float
    ) -> None:
        """POST a firing alert to Alertmanager."""
        alert = {
            "labels": {
                "alertname": "JulaboTemperatureDeviation",
                "chiller_id": self._chiller_id,
                "severity": "warning",
            },
            "annotations": {
                "summary": (
                    f"Temperature {temperature:.1f} deviates from "
                    f"setpoint {setpoint:.1f} by more than {threshold:.1f}"
                ),
            },
        }
        self._post([alert])

    def send_resolved(self, temperature: float, setpoint: float) -> None:
        """POST a resolved alert to Alertmanager."""
        alert = {
            "labels": {
                "alertname": "JulaboTemperatureDeviation",
                "chiller_id": self._chiller_id,
                "severity": "warning",
            },
            "annotations": {
                "summary": (
                    f"Temperature {temperature:.1f} back within range of "
                    f"setpoint {setpoint:.1f}"
                ),
            },
            "endsAt": datetime.now(timezone.utc).isoformat(),
        }
        self._post([alert])

    def _post(self, alerts: list[dict[str, Any]]) -> None:
        """Send alerts to the Alertmanager v2 API."""
        try:
            data = json.dumps(alerts).encode("utf-8")
            req = urllib.request.Request(
                f"{self._url}/api/v2/alerts",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception as exc:
            LOGGER.warning("Alertmanager POST failed: %s", exc)

_LOG_HEADER = ["timestamp", "event", "temperature", "setpoint", "deviation", "threshold"]


class TemperatureAlarm:
    """Monitors the deviation between process temperature and setpoint."""

    def __init__(
        self,
        threshold: float = 2.0,
        on_alarm: Callable[[], None] | None = None,
        on_clear: Callable[[], None] | None = None,
        *,
        desktop_notifications: bool = False,
        log_file: str | None = None,
        alertmanager_client: AlertmanagerClient | None = None,
    ):
        self.threshold = threshold
        self._on_alarm = on_alarm
        self._on_clear = on_clear
        self._alarming = False
        self._desktop_notifications = desktop_notifications
        self._log_file_path = log_file
        self._log_fh: IO[str] | None = None
        self._log_writer: Any = None
        self._alertmanager = alertmanager_client

    @property
    def is_alarming(self) -> bool:
        return self._alarming

    def _ensure_log_open(self) -> Any | None:
        """Open the log file if needed and return the CSV writer."""
        if self._log_file_path is None:
            return None
        if self._log_fh is None or self._log_fh.closed:
            import os

            write_header = (
                not os.path.exists(self._log_file_path)
                or os.path.getsize(self._log_file_path) == 0
            )
            self._log_fh = open(self._log_file_path, "a", newline="")
            self._log_writer = csv.writer(self._log_fh)
            if write_header:
                self._log_writer.writerow(_LOG_HEADER)
                self._log_fh.flush()
        return self._log_writer

    def _log_event(
        self, event: str, temperature: float, setpoint: float
    ) -> None:
        """Append a CSV row to the alarm log file."""
        try:
            writer = self._ensure_log_open()
            if writer is None:
                return
            ts = datetime.now(timezone.utc).isoformat()
            deviation = abs(temperature - setpoint)
            writer.writerow([
                ts, event,
                f"{temperature:.2f}", f"{setpoint:.2f}",
                f"{deviation:.2f}", f"{self.threshold:.2f}",
            ])
            if self._log_fh is not None:
                self._log_fh.flush()
        except OSError:
            pass

    def close(self) -> None:
        """Close the alarm log file handle."""
        if self._log_fh is not None and not self._log_fh.closed:
            self._log_fh.close()
        self._log_fh = None
        self._log_writer = None

    def check(self, temperature: float, setpoint: float) -> bool:
        """Return ``True`` if the deviation exceeds the threshold.

        Triggers callbacks on state transitions.  A threshold of ``0``
        disables the alarm entirely.
        """
        if self.threshold <= 0:
            if self._alarming:
                self._alarming = False
                LOGGER.info("Alarm cleared (disabled)")
                if self._on_clear is not None:
                    self._on_clear()
            return False

        deviation = abs(temperature - setpoint)
        alarming = deviation > self.threshold

        if alarming and not self._alarming:
            self._alarming = True
            LOGGER.warning(
                "Temperature alarm: %.2f °C deviates from setpoint %.2f °C by %.2f °C "
                "(threshold %.2f °C)",
                temperature,
                setpoint,
                deviation,
                self.threshold,
            )
            self._log_event("ALARM", temperature, setpoint)
            if self._desktop_notifications:
                from .notifications import send_desktop_notification

                send_desktop_notification(
                    "Julabo Temperature Alarm",
                    f"Temperature {temperature:.1f} °C deviates from "
                    f"setpoint {setpoint:.1f} °C by {deviation:.1f} °C",
                )
            if self._alertmanager is not None:
                try:
                    self._alertmanager.send_firing(temperature, setpoint, self.threshold)
                except Exception:
                    LOGGER.warning("Alertmanager send_firing failed", exc_info=True)
            if self._on_alarm is not None:
                self._on_alarm()
        elif not alarming and self._alarming:
            self._alarming = False
            LOGGER.info("Temperature alarm cleared")
            self._log_event("CLEAR", temperature, setpoint)
            if self._alertmanager is not None:
                try:
                    self._alertmanager.send_resolved(temperature, setpoint)
                except Exception:
                    LOGGER.warning("Alertmanager send_resolved failed", exc_info=True)
            if self._on_clear is not None:
                self._on_clear()

        return self._alarming
