"""Cross-platform desktop notifications with click-to-open.

Uses desktop-notifier (WinRT toasts on Windows, Notification Center on macOS,
D-Bus on Linux), which runs on an asyncio loop in its own thread. If it can't
be used, falls back to a plain notification with no click action.
"""

import asyncio
import logging
import os
import subprocess
import sys
import threading

from paths import APP_NAME, resource

log = logging.getLogger("notify")


class Notifier:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="notify").start()
        self.native = None
        try:
            from desktop_notifier import DesktopNotifier, Icon
            if sys.platform == "darwin":
                from desktop_notifier.backends.macos_support import is_bundle
                if not is_bundle():
                    raise RuntimeError("Notification Center needs the packaged .app")
            # desktop-notifier must be created on the loop it will run callbacks on.
            self.native = asyncio.run_coroutine_threadsafe(
                self._create(DesktopNotifier, Icon), self.loop).result(10)
        except Exception as e:
            log.info("Using basic notifications (%s)", e)

    async def _create(self, DesktopNotifier, Icon):
        return DesktopNotifier(app_name=APP_NAME, app_icon=Icon(path=resource("assets", "icon.png")))

    def show(self, title, body, on_click=None, sound=True):
        if self.native:
            fut = asyncio.run_coroutine_threadsafe(self._send(title, body, on_click, sound), self.loop)
            fut.add_done_callback(lambda f: f.exception() and self._fallback(title, body, f.exception()))
        else:
            threading.Thread(target=basic_notification, args=(title, body, sound), daemon=True).start()

    async def _send(self, title, body, on_click, sound):
        from desktop_notifier import DEFAULT_SOUND, Button
        buttons = [Button(title="Show on map", on_pressed=on_click)] if on_click else []
        await self.native.send(title=title, message=body, on_clicked=on_click, buttons=buttons,
                               sound=DEFAULT_SOUND if sound else None)

    def _fallback(self, title, body, err):
        log.warning("Notification failed (%s); using basic notification", err)
        basic_notification(title, body, True)


def basic_notification(title, body, sound=True):
    try:
        if sys.platform == "win32":
            _windows_toast(title, body, sound)
        elif sys.platform == "darwin":
            script = f"display notification {_as_str(body)} with title {_as_str(title)}"
            if sound:
                script += ' sound name "Glass"'
            subprocess.run(["osascript", "-e", script], check=False)
        else:
            subprocess.run(["notify-send", "--app-name", APP_NAME, title, body], check=False)
    except Exception as e:
        log.warning("Basic notification failed: %s", e)


def _as_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _xml(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _windows_toast(title, body, sound):
    audio = '<audio src="ms-winsoundevent:Notification.Reminder"/>' if sound else '<audio silent="true"/>'
    xml = (f'<toast><visual><binding template="ToastGeneric"><text>{_xml(title)}</text>'
           f'<text>{_xml(body)}</text></binding></visual>{audio}</toast>')
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml($env:PO_XML);"
        "$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id)"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                   env={**os.environ, "PO_XML": xml}, check=False,
                   creationflags=subprocess.CREATE_NO_WINDOW)
