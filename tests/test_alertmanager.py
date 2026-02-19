"""Tests for AlertmanagerClient in julabo_control.alarm."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from julabo_control.alarm import AlertmanagerClient, TemperatureAlarm


class TestAlertmanagerClient:
    def test_post_fires_http_request(self) -> None:
        with patch("julabo_control.alarm.urllib.request.urlopen") as mock_open:
            client = AlertmanagerClient("http://localhost:9093")
            client.send_firing(25.0, 20.0, 2.0)
        mock_open.assert_called_once()
        req = mock_open.call_args[0][0]
        assert req.full_url == "http://localhost:9093/api/v2/alerts"
        body = json.loads(req.data)
        assert len(body) == 1
        assert body[0]["labels"]["alertname"] == "JulaboTemperatureDeviation"

    def test_post_handles_connection_failure(self) -> None:
        with patch(
            "julabo_control.alarm.urllib.request.urlopen",
            side_effect=ConnectionError("refused"),
        ):
            client = AlertmanagerClient("http://localhost:9093")
            # Should not raise
            client.send_firing(25.0, 20.0, 2.0)

    def test_send_firing_labels(self) -> None:
        with patch("julabo_control.alarm.urllib.request.urlopen") as mock_open:
            client = AlertmanagerClient("http://localhost:9093", chiller_id="ch1")
            client.send_firing(30.0, 20.0, 5.0)
        body = json.loads(mock_open.call_args[0][0].data)
        assert body[0]["labels"]["chiller_id"] == "ch1"
        assert body[0]["labels"]["severity"] == "warning"

    def test_send_resolved_has_ends_at(self) -> None:
        with patch("julabo_control.alarm.urllib.request.urlopen") as mock_open:
            client = AlertmanagerClient("http://localhost:9093")
            client.send_resolved(20.5, 20.0)
        body = json.loads(mock_open.call_args[0][0].data)
        assert "endsAt" in body[0]


class TestAlarmWithAlertmanager:
    def test_alertmanager_send_firing_called(self) -> None:
        am = MagicMock(spec=AlertmanagerClient)
        alarm = TemperatureAlarm(threshold=1.0, alertmanager_client=am)
        alarm.check(25.0, 20.0)  # deviation=5 > threshold=1
        am.send_firing.assert_called_once_with(25.0, 20.0, 1.0)

    def test_alertmanager_send_resolved_called(self) -> None:
        am = MagicMock(spec=AlertmanagerClient)
        alarm = TemperatureAlarm(threshold=1.0, alertmanager_client=am)
        alarm.check(25.0, 20.0)  # Trigger alarm
        am.reset_mock()
        alarm.check(20.5, 20.0)  # Clear alarm (deviation=0.5 < 1.0)
        am.send_resolved.assert_called_once_with(20.5, 20.0)

    def test_alertmanager_failure_does_not_propagate(self) -> None:
        am = MagicMock(spec=AlertmanagerClient)
        am.send_firing.side_effect = RuntimeError("connection refused")
        alarm = TemperatureAlarm(threshold=1.0, alertmanager_client=am)
        # Should not raise
        alarm.check(25.0, 20.0)
        assert alarm.is_alarming
