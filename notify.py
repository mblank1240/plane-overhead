"""Cross-platform desktop notifications with click-to-open.

Uses desktop-notifier (WinRT toasts on Windows, Notification Center on macOS,
D-Bus on Linux), which runs on an asyncio loop in its own thread. If it can't
be used, falls back to a plain notification with no click action.

No PowerShell or other script hosts are spawned: hidden PowerShell launched by an
unsigned app is a classic malware pattern and got the project quarantined.
"""

import asyncio
import logging
import subprocess
import sys
import threading

from paths import APP_NAME, resource

log = logging.getLogger("notify")


class Notifier:
    def __init__(self, fallback=None):
        """fallback(title, body) shows a notification some other way (the tray icon)."""
        self.fallback = fallback
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
            threading.Thread(target=self._basic, args=(title, body, sound), daemon=True).start()

    async def _send(self, title, body, on_click, sound):
        from desktop_notifier import DEFAULT_SOUND, Button
        buttons = [Button(title="Show on map", on_pressed=on_click)] if on_click else []
        await self.native.send(title=title, message=body, on_clicked=on_click, buttons=buttons,
                               sound=DEFAULT_SOUND if sound else None)

    def _fallback(self, title, body, err):
        log.warning("Notification failed (%s); using basic notification", err)
        self._basic(title, body, True)

    def _basic(self, title, body, sound):
        if sys.platform == "win32":
            if self.fallback:
                self.fallback(title, body)
            else:
                log.warning("No notification method available: %s", title)
            return
        basic_notification(title, body, sound)


def basic_notification(title, body, sound=True):
    try:
        if sys.platform == "darwin":
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

