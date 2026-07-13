"""Tests for julabo_control.notifications."""

from __future__ import annotations

import subprocess as _sp
from unittest.mock import MagicMock, patch

from julabo_control.notifications import (
    _escape_applescript,
    _escape_powershell_single_quoted,
    send_desktop_notification,
)


class TestEscapeApplescript:
    def test_plain_text(self) -> None:
        assert _escape_applescript("hello") == "hello"

    def test_double_quotes(self) -> None:
        assert _escape_applescript('say "hi"') == 'say \\"hi\\"'

    def test_backslash(self) -> None:
        assert _escape_applescript("a\\b") == "a\\\\b"

    def test_shell_metacharacters(self) -> None:
        result = _escape_applescript('"; rm -rf /')
        assert "\\" in result or result == '\\"; rm -rf /'


class TestEscapePowerShell:
    def test_plain_text(self) -> None:
        assert _escape_powershell_single_quoted("hello") == "hello"

    def test_single_quote_doubled(self) -> None:
        assert _escape_powershell_single_quoted("it's") == "it''s"

    def test_xml_chars_passed_through(self) -> None:
        # CreateTextNode sets text content; special chars are NOT entity-escaped.
        assert _escape_powershell_single_quoted("<a> & b") == "<a> & b"

    def test_injection_quote_neutralized(self) -> None:
        result = _escape_powershell_single_quoted("'; rm -rf /")
        assert result == "''; rm -rf /"


class TestDesktopNotification:
    @patch("julabo_control.notifications.sys")
    @patch("julabo_control.notifications.subprocess")
    def test_linux(self, mock_subprocess: MagicMock, mock_sys: MagicMock) -> None:
        mock_sys.platform = "linux"
        mock_subprocess.SubprocessError = _sp.SubprocessError

        result = send_desktop_notification("Title", "Body")
        assert result is True
        mock_subprocess.run.assert_called_once()

    @patch("julabo_control.notifications.sys")
    @patch("julabo_control.notifications.subprocess")
    def test_darwin(self, mock_subprocess: MagicMock, mock_sys: MagicMock) -> None:
        mock_sys.platform = "darwin"
        mock_subprocess.SubprocessError = _sp.SubprocessError

        result = send_desktop_notification("Title", "Body")
        assert result is True
        call_kwargs = mock_subprocess.run.call_args
        # osascript receives script via stdin, not -e
        assert call_kwargs[0][0] == ["osascript"]
        assert call_kwargs[1]["input"] is not None

    @patch("julabo_control.notifications.sys")
    @patch("julabo_control.notifications.subprocess")
    def test_darwin_escapes_quotes(
        self, mock_subprocess: MagicMock, mock_sys: MagicMock
    ) -> None:
        mock_sys.platform = "darwin"
        mock_subprocess.SubprocessError = _sp.SubprocessError

        send_desktop_notification('He said "hello"', 'It\'s a "test"')
        call_kwargs = mock_subprocess.run.call_args
        script = call_kwargs[1]["input"].decode("utf-8")
        # Double quotes in the original should be escaped
        assert '\\"hello\\"' in script
        assert '\\"test\\"' in script

    @patch("julabo_control.notifications.sys")
    @patch("julabo_control.notifications.subprocess")
    def test_darwin_shell_injection(
        self, mock_subprocess: MagicMock, mock_sys: MagicMock
    ) -> None:
        mock_sys.platform = "darwin"
        mock_subprocess.SubprocessError = _sp.SubprocessError

        send_desktop_notification('"; rm -rf /', "body")
        call_kwargs = mock_subprocess.run.call_args
        script = call_kwargs[1]["input"].decode("utf-8")
        # The injected quote should be escaped
        assert '\\"; rm -rf /' in script

    @patch("julabo_control.notifications.sys")
    @patch("julabo_control.notifications.subprocess")
    def test_win32(self, mock_subprocess: MagicMock, mock_sys: MagicMock) -> None:
        mock_sys.platform = "win32"
        mock_subprocess.SubprocessError = _sp.SubprocessError

        result = send_desktop_notification("Title", "Body")
        assert result is True

    @patch("julabo_control.notifications.sys")
    @patch("julabo_control.notifications.subprocess")
    def test_win32_preserves_text_content(
        self, mock_subprocess: MagicMock, mock_sys: MagicMock
    ) -> None:
        mock_sys.platform = "win32"
        mock_subprocess.SubprocessError = _sp.SubprocessError

        # CreateTextNode sets XML text content, so special chars are passed
        # through literally (the DOM escapes on serialization) rather than as
        # entity codes.
        send_desktop_notification("<script>alert</script>", "a & b")
        call_kwargs = mock_subprocess.run.call_args
        ps_cmd = call_kwargs[0][0][2]  # powershell -Command <script>
        assert "<script>alert</script>" in ps_cmd
        assert "a & b" in ps_cmd

    @patch("julabo_control.notifications.sys")
    @patch("julabo_control.notifications.subprocess")
    def test_win32_escapes_single_quotes(
        self, mock_subprocess: MagicMock, mock_sys: MagicMock
    ) -> None:
        mock_sys.platform = "win32"
        mock_subprocess.SubprocessError = _sp.SubprocessError

        # A single quote must be doubled so it cannot break out of the
        # PowerShell single-quoted string literal.
        send_desktop_notification("it's", "o'clock")
        call_kwargs = mock_subprocess.run.call_args
        ps_cmd = call_kwargs[0][0][2]
        assert "it''s" in ps_cmd
        assert "o''clock" in ps_cmd

    @patch("julabo_control.notifications.subprocess")
    @patch("julabo_control.notifications.sys")
    def test_unsupported_platform(
        self, mock_sys: MagicMock, mock_subprocess: MagicMock
    ) -> None:
        mock_sys.platform = "freebsd"
        mock_subprocess.SubprocessError = _sp.SubprocessError

        result = send_desktop_notification("Title", "Body")
        assert result is False

    @patch("julabo_control.notifications.sys")
    @patch("julabo_control.notifications.subprocess")
    def test_failure_returns_false(
        self, mock_subprocess: MagicMock, mock_sys: MagicMock
    ) -> None:
        mock_sys.platform = "linux"
        mock_subprocess.SubprocessError = _sp.SubprocessError
        mock_subprocess.run.side_effect = FileNotFoundError("notify-send not found")

        result = send_desktop_notification("Title", "Body")
        assert result is False
