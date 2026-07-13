"""Cross-platform desktop notification helpers."""

from __future__ import annotations

import logging
import subprocess
import sys

LOGGER = logging.getLogger(__name__)


def _escape_applescript(text: str) -> str:
    """Escape a string for safe inclusion in an AppleScript literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _escape_powershell_single_quoted(text: str) -> str:
    """Escape a string for a PowerShell single-quoted literal.

    The value is passed to ``CreateTextNode`` (which sets XML *text content*,
    not markup, so no XML entity escaping is needed or wanted — that would make
    special characters display as literal entity codes).  In a single-quoted
    PowerShell string only the quote itself is special; double it to escape.
    """
    return text.replace("'", "''")


def send_desktop_notification(title: str, message: str) -> bool:
    """Show an OS-level desktop notification.

    Returns ``True`` if the notification was sent successfully, ``False``
    otherwise.  Failures are logged but never raised so that a missing
    notification tool does not crash the application.
    """
    try:
        if sys.platform == "darwin":
            safe_title = _escape_applescript(title)
            safe_message = _escape_applescript(message)
            script = f'display notification "{safe_message}" with title "{safe_title}"'
            subprocess.run(
                ["osascript"],
                input=script.encode("utf-8"),
                check=True,
                capture_output=True,
                timeout=5,
            )
            return True

        if sys.platform.startswith("linux"):
            subprocess.run(
                ["notify-send", title, message],
                check=True,
                capture_output=True,
                timeout=5,
            )
            return True

        if sys.platform == "win32":
            safe_title = _escape_powershell_single_quoted(title)
            safe_message = _escape_powershell_single_quoted(message)
            ps_script = (
                "[Windows.UI.Notifications.ToastNotificationManager, "
                "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
                "$template = [Windows.UI.Notifications.ToastNotificationManager]::"
                "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::"
                "ToastText02); "
                "$textNodes = $template.GetElementsByTagName('text'); "
                f"$textNodes.Item(0).AppendChild($template.CreateTextNode('{safe_title}')); "
                f"$textNodes.Item(1).AppendChild($template.CreateTextNode('{safe_message}')); "
                "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
                "[Windows.UI.Notifications.ToastNotificationManager]::"
                "CreateToastNotifier('Julabo Control').Show($toast)"
            )
            subprocess.run(
                ["powershell", "-Command", ps_script],
                check=True,
                capture_output=True,
                timeout=10,
            )
            return True

        LOGGER.debug("Desktop notifications not supported on %s", sys.platform)
        return False

    except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
        LOGGER.debug("Failed to send desktop notification: %s", exc)
        return False
