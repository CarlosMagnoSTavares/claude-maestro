"""Notificações: feed interno (banco) + toast nativo do Windows (best-effort)."""
import subprocess

from . import db

_TOAST_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $xml.GetElementsByTagName("text")
$texts.Item(0).AppendChild($xml.CreateTextNode($env:MAESTRO_TITLE)) | Out-Null
$texts.Item(1).AppendChild($xml.CreateTextNode($env:MAESTRO_MSG)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Claude Maestro").Show($toast)
"""


def _windows_toast(title: str, message: str):
    try:
        import os
        env = os.environ.copy()
        env["MAESTRO_TITLE"] = title[:100]
        env["MAESTRO_MSG"] = message[:200]
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
            capture_output=True, timeout=20, env=env,
            creationflags=config.CREATE_NO_WINDOW,
        )
    except Exception:
        pass  # toast é best-effort; o feed interno é o canal principal


def notify(title: str, message: str = "", toast: bool = True):
    """Registra no feed do app e tenta toast nativo do Windows."""
    db.add_notification(title, message)
    if toast:
        _windows_toast(title, message)
