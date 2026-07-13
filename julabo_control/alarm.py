"""Temperature alarm support for Julabo applications."""

from __future__ import annotations

import csv
import json
import logging
import threading
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
        hysteresis: float | None = None,
        async_notifications: bool = False,
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
        # Deadband to prevent alarm chatter when the temperature hovers around
        # the threshold: alarm engages above ``threshold`` and only clears once
        # the deviation falls back below ``threshold - hysteresis``.  Defaults
        # to 10% of the threshold.
        self._hysteresis = hysteresis
        # When enabled, blocking notification I/O (desktop toast subprocess and
        # Alertmanager HTTP POST) runs in a daemon thread so a slow/unreachable
        # endpoint never freezes the caller (e.g. the Tk event loop).
        self._async_notifications = async_notifications

    @property
    def is_alarming(self) -> bool:
        return self._alarming

    @property
    def _clear_threshold(self) -> float:
        band = self._hysteresis if self._hysteresis is not None else abs(self.threshold) * 0.1
        return self.threshold - band

    def _dispatch(self, func: Callable[[], None]) -> None:
        """Run a (possibly blocking) notification callable sync or off-thread."""
        if self._async_notifications:
            threading.Thread(target=func, daemon=True).start()
        else:
            func()

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
        except OSError as exc:
            # Never raise from the alarm path, but do not silently drop the
            # audit-trail failure either — surface it so a gap is detectable.
            LOGGER.warning("Alarm log write failed (%s): %s", self._log_file_path, exc)

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
        # Hysteresis: engage above ``threshold``, clear only once the deviation
        # falls back below ``threshold - hysteresis``.  In between, hold state.
        if not self._alarming and deviation > self.threshold:
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
                dev = deviation
                temp = temperature
                sp = setpoint

                def _notify() -> None:
                    from .notifications import send_desktop_notification

                    send_desktop_notification(
                        "Julabo Temperature Alarm",
                        f"Temperature {temp:.1f} °C deviates from "
                        f"setpoint {sp:.1f} °C by {dev:.1f} °C",
                    )

                self._dispatch(_notify)
            if self._alertmanager is not None:
                am = self._alertmanager

                def _fire() -> None:
                    try:
                        am.send_firing(temperature, setpoint, self.threshold)
                    except Exception:
                        LOGGER.warning("Alertmanager send_firing failed", exc_info=True)

                self._dispatch(_fire)
            if self._on_alarm is not None:
                self._on_alarm()
        elif self._alarming and deviation <= self._clear_threshold:
            self._alarming = False
            LOGGER.info("Temperature alarm cleared")
            self._log_event("CLEAR", temperature, setpoint)
            if self._alertmanager is not None:
                am = self._alertmanager

                def _resolve() -> None:
                    try:
                        am.send_resolved(temperature, setpoint)
                    except Exception:
                        LOGGER.warning("Alertmanager send_resolved failed", exc_info=True)

                self._dispatch(_resolve)
            if self._on_clear is not None:
                self._on_clear()

        return self._alarming
