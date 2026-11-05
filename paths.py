"""Where the app's bundled files and per-user data live on each OS."""

import os
import sys
from pathlib import Path

APP_NAME = "Plane Overhead"
FROZEN = getattr(sys, "frozen", False)
SOURCE_DIR = Path(__file__).resolve().parent


def resource(*parts):
    """A file shipped with the app (web UI, icons). PyInstaller unpacks these to _MEIPASS."""
    return Path(getattr(sys, "_MEIPASS", SOURCE_DIR)).joinpath(*parts)


def data_dir():
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        d = base / APP_NAME
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        d = base / "plane-overhead"
    d.mkdir(parents=True, exist_ok=True)
    return d


def launch_command(*args):
    """The command that starts this app again, as a list (works from source or a build)."""
    if FROZEN:
        return [sys.executable, *args]
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower() == "python.exe":
        exe = exe.with_name("pythonw.exe")  # no console window
    return [str(exe), str(SOURCE_DIR / "main.py"), *args]
