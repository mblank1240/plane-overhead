"""Local HTTP server for the map UI and its JSON API (127.0.0.1 only)."""

import json
import logging
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from paths import resource

HOST, PORT = "127.0.0.1", 8737
BASE_URL = f"http://{HOST}:{PORT}/"
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

log = logging.getLogger("server")


class Handler(SimpleHTTPRequestHandler):
    app = None  # the running App (main.py), set before serving

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(resource("web")), **kwargs)

    def log_message(self, fmt, *args):
        pass  # keep the log for alerts and errors

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def guard(self, mutating=False):
        # Only answer requests addressed to us by name (blocks DNS rebinding), and
        # require a custom header on changes (blocks cross-site form posts).
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            self.send_error(403)
            return False
        if mutating and self.headers.get("X-Plane-Overhead") != "1":
            self.send_error(403)
            return False
        return True

    def do_GET(self):
        if not self.guard():
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/ping":
            return self.send_json({"app": "plane-overhead"})
        if url.path == "/api/state":
            return self.send_json(self.app.state(query.get("trail", [None])[0]))
        if url.path == "/api/window/poll":
            after = int(query.get("after", ["-1"])[0])
            latest, commands = self.app.window_commands(after)
            return self.send_json({"latest": latest, "commands": commands})
        return super().do_GET()

    def do_POST(self):
        if not self.guard(mutating=True):
            return
        path = urlparse(self.path).path
        tracker = self.app.tracker
        try:
            data = self.read_json()
            if path == "/api/settings":
                tracker.update_settings(data)
                return self.send_json(tracker.settings)
            if path == "/api/favorites":
                return self.send_json(tracker.add_favorite(data))
            if path == "/api/favorites/remove":
                tracker.remove_favorite(data.get("id"))
                return self.send_json({"ok": True})
            if path == "/api/pause":
                tracker.pause(float(data.get("minutes") or 0))
                self.app.refresh_tray()
                return self.send_json({"ok": True})
            if path == "/api/autostart":
                self.app.set_autostart(bool(data.get("enabled")))
                return self.send_json({"ok": True})
            if path == "/api/test-notification":
                self.app.test_notification()
                return self.send_json({"ok": True})
            if path == "/api/window/open":
                self.app.open_window(data.get("hex"))
                return self.send_json({"ok": True})
            if path == "/api/quit":
                self.send_json({"ok": True})
                return self.app.quit()
        except (ValueError, KeyError, TypeError) as e:
            return self.send_json({"error": str(e)}, 400)
        self.send_error(404)


def make_server(app):
    Handler.app = app
    return ThreadingHTTPServer((HOST, PORT), Handler)
