"""The app window: a native webview showing the map UI, run as its own process.

The background app starts this process when you open the app or click a
notification, and sends it "focus this plane" commands while it's open.
"""

import json
import logging
import os
import sys
import threading
import urllib.request
import webbrowser

from paths import APP_NAME, data_dir
from server import BASE_URL

log = logging.getLogger("window")


def poll_commands(win, closed):
    last = -1  # first poll just learns the latest id, so old commands aren't replayed
    while not closed.is_set():
        try:
            with urllib.request.urlopen(f"{BASE_URL}api/window/poll?after={last}", timeout=5) as r:
                reply = json.load(r)
        except OSError:
            closed.wait(2)
            continue
        last = reply["latest"]
        for cmd in reply["commands"]:
            bring_to_front(win)
            if cmd.get("hex"):
                win.evaluate_js(f"window.openPlane && window.openPlane({json.dumps(cmd['hex'])})")
        closed.wait(0.75)


def bring_to_front(win):
    try:
        win.restore()
        win.show()
        win.on_top = True
        win.on_top = False
    except Exception as e:
        log.debug("bring_to_front: %s", e)


def run(url):
    try:
        import webview
    except ImportError as e:
        log.warning("No webview available (%s); opening in browser", e)
        webbrowser.open(url)
        return

    if sys.platform == "darwin":
        # The bundle is a menu-bar-only app (LSUIElement) so the background process
        # has no Dock icon; the window process should have one.
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyRegular
            NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyRegular)
        except Exception:
            pass

    win = webview.create_window(APP_NAME, url, width=1400, height=900, min_size=(760, 520))
    closed = threading.Event()
    win.events.closed += closed.set
    try:
        # pywebview runs poll_commands on its own (non-daemon) thread once the window is up.
        webview.start(poll_commands, (win, closed), private_mode=False,
                      storage_path=str(data_dir() / "webview"))
    except Exception as e:
        log.warning("Webview failed (%s); opening in browser", e)
        webbrowser.open(url)
    # The window is gone. Exit for real: if this process lingered, the background app
    # would think the window was still open and never start a new one.
    logging.shutdown()
    os._exit(0)
