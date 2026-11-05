"""Plane Overhead entry point.

    Plane Overhead               start (or reuse) the background app and open the window
    Plane Overhead --background  start the background app only (used at login)
    Plane Overhead --window URL  internal: the window process

The background app runs the tracker, the local server, notifications, and the
tray / menu bar icon. The window is a separate process so either can run
without the other, and so each gets its own main thread (macOS requires the
tray icon and the webview to each own one).
"""

import json
import logging
import subprocess
import sys
import threading
import urllib.request

import integration
from paths import APP_NAME, data_dir, launch_command, resource
from server import BASE_URL, make_server

log = logging.getLogger("main")


def setup_logging(name):
    handlers = [logging.FileHandler(data_dir() / name, encoding="utf-8")]
    if sys.stdout:  # pythonw and windowed builds have no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def ask_running_app(path, body=None):
    """Talk to an already-running background app. Returns None if none is running."""
    req = urllib.request.Request(BASE_URL + path.lstrip("/"))
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Plane-Overhead", "1")
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.load(r)
    except OSError:
        return None


class App:
    def __init__(self):
        from datasets import Datasets
        from notify import Notifier
        from tracker import Tracker

        self.notifier = Notifier(fallback=self.tray_notification)
        self.datasets = Datasets(data_dir())
        self.tracker = Tracker(data_dir(), on_alert=self.notify_alert, datasets=self.datasets)
        self.window_proc = None
        self.commands = []        # [{"id", "hex"}] for the open window
        self.command_id = 0
        self.lock = threading.Lock()
        self.tray = None
        self.stopped = threading.Event()
        self.server = make_server(self)

        # Start-at-login is opt-in (Settings or tray menu). Turning it on silently at first
        # launch looked like malware persistence to antivirus, so the app never does that.
        threading.Thread(target=integration.register_launcher, daemon=True).start()

    # ---- state for the UI ----

    def state(self, trail):
        s = self.tracker.state(trail)
        s["autostart"] = integration.autostart_enabled()
        s["settings"].pop("_installed", None)
        return s

    # ---- window ----

    def open_window(self, hex_=None):
        with self.lock:
            if self.window_proc and self.window_proc.poll() is None:
                self.command_id += 1
                self.commands = (self.commands + [{"id": self.command_id, "hex": hex_}])[-20:]
                return
            url = BASE_URL + (f"#plane={hex_}" if hex_ else "")
            self.window_proc = subprocess.Popen(launch_command("--window", url))

    def window_commands(self, after):
        with self.lock:
            if after < 0:
                return self.command_id, []
            return self.command_id, [c for c in self.commands if c["id"] > after]

    # ---- notifications ----

    def notify_alert(self, alert, sound):
        self.notifier.show(alert["title"], alert["body"],
                           on_click=lambda: self.open_window(alert["hex"]), sound=sound)

    def tray_notification(self, title, body):
        """Last-resort notification through the tray icon (balloon on Windows)."""
        if self.tray and getattr(self.tray, "HAS_NOTIFICATION", False):
            self.tray.notify(body, title)

    def test_notification(self):
        self.notifier.show("✈ Test: N12345 (C172) overhead in 3:00",
                           "1,500 ft above you, passing within 0.3 mi. Now 9 mi away at 110 kt.",
                           on_click=lambda: self.open_window(), sound=self.tracker.settings["sound"])

    # ---- settings that live outside the tracker ----

    def set_autostart(self, enabled):
        try:
            integration.set_autostart(enabled)
        except Exception as e:
            log.warning("Could not change start-at-login: %s", e)
        self.refresh_tray()

    # ---- tray / menu bar ----

    def refresh_tray(self):
        if self.tray:
            self.tray.update_menu()

    def build_tray(self):
        import pystray
        from PIL import Image

        def toggle_pause(_icon, _item):
            self.tracker.pause(0 if self.tracker.paused() else 60)

        menu = pystray.Menu(
            pystray.MenuItem(f"Open {APP_NAME}", lambda: self.open_window(), default=True),
            pystray.MenuItem("Pause alerts for 1 hour", toggle_pause,
                             checked=lambda _: self.tracker.paused()),
            pystray.MenuItem("Start at login", lambda: self.set_autostart(not integration.autostart_enabled()),
                             checked=lambda _: integration.autostart_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self.quit()),
        )
        image = Image.open(resource("assets", "icon.png"))
        return pystray.Icon("plane-overhead", image, APP_NAME, menu)

    def quit(self):
        log.info("Quitting")
        if self.window_proc and self.window_proc.poll() is None:
            self.window_proc.terminate()
        self.stopped.set()
        if self.tray:
            self.tray.stop()
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    # ---- main loop ----

    def run(self, open_window):
        threading.Thread(target=self.datasets.run_forever, daemon=True, name="datasets").start()
        threading.Thread(target=self.tracker.run_forever, daemon=True, name="tracker").start()
        threading.Thread(target=self.server.serve_forever, daemon=True, name="server").start()
        log.info("%s running at %s", APP_NAME, BASE_URL)
        if open_window:
            self.open_window()
        try:
            self.tray = self.build_tray()
            self.tray.run()  # blocks on the main thread until Quit
        except Exception as e:
            # No tray on this desktop (e.g. GNOME without the AppIndicator extension).
            # Keep running; the window has a Quit button in Settings.
            log.warning("No tray icon (%s); running without one", e)
            self.tray = None
            self.stopped.wait()


def main():
    args = sys.argv[1:]
    if args[:1] == ["--window"]:
        setup_logging("window.log")
        import window
        window.run(args[1] if len(args) > 1 else BASE_URL)
        return

    background = "--background" in args
    if ask_running_app("api/ping"):
        if not background:
            ask_running_app("api/window/open", {})
        return

    setup_logging("app.log")
    try:
        app = App()
    except OSError as e:
        log.error("Could not start (%s). Is another copy running?", e)
        return
    app.run(open_window=not background)


if __name__ == "__main__":
    main()
