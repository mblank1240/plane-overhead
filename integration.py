"""OS integration: start at login, and an app launcher entry (Start Menu / applications menu).

Windows: HKCU Run key (only when the user turns it on). No Start Menu shortcut yet:
         making one needs PowerShell/COM, and hidden PowerShell got the app quarantined
         by antivirus. A proper installer should add the shortcut instead.
macOS:   LaunchAgent in ~/Library/LaunchAgents (drag the .app to Applications yourself).
Linux:   XDG autostart entry + ~/.local/share/applications entry.
"""

import logging
import os
import plistlib
import sys
from pathlib import Path

from paths import APP_NAME, launch_command, resource

log = logging.getLogger("integration")
MAC_AGENT = Path.home() / "Library" / "LaunchAgents" / "net.planeoverhead.app.plist"
XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
XDG_DATA = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
LINUX_AUTOSTART = XDG_CONFIG / "autostart" / "plane-overhead.desktop"
LINUX_MENU = XDG_DATA / "applications" / "plane-overhead.desktop"
WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _quote_cmd(cmd):
    return " ".join(f'"{c}"' for c in cmd)


def _desktop_entry(cmd, autostart):
    exec_line = " ".join('"' + c.replace('"', '\\"') + '"' for c in cmd)
    lines = [
        "[Desktop Entry]", "Type=Application", f"Name={APP_NAME}",
        "Comment=Alerts before low planes pass overhead", f"Exec={exec_line}",
        f"Icon={resource('assets', 'icon.png')}", "Categories=Utility;", "Terminal=false",
    ]
    if autostart:
        lines.append("X-GNOME-Autostart-enabled=true")
    return "\n".join(lines) + "\n"


# ---- start at login -----------------------------------------------------------

def autostart_enabled():
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY) as k:
                winreg.QueryValueEx(k, APP_NAME)
            return True
        if sys.platform == "darwin":
            return MAC_AGENT.exists()
        return LINUX_AUTOSTART.exists()
    except OSError:
        return False


def set_autostart(enabled):
    cmd = launch_command("--background")
    if sys.platform == "win32":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY) as k:
            if enabled:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _quote_cmd(cmd))
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    elif sys.platform == "darwin":
        if enabled:
            MAC_AGENT.parent.mkdir(parents=True, exist_ok=True)
            MAC_AGENT.write_bytes(plistlib.dumps({
                "Label": "net.planeoverhead.app",
                "ProgramArguments": cmd,
                "RunAtLoad": True,
                "ProcessType": "Interactive",
            }))
        else:
            MAC_AGENT.unlink(missing_ok=True)
    else:
        if enabled:
            LINUX_AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
            LINUX_AUTOSTART.write_text(_desktop_entry(cmd, autostart=True))
        else:
            LINUX_AUTOSTART.unlink(missing_ok=True)


# ---- launcher entry -------------------------------------------------------------

def register_launcher():
    """Add the app to the Start Menu (Windows) or applications menu (Linux). Safe to repeat."""
    cmd = launch_command()
    try:
        if sys.platform.startswith("linux"):
            LINUX_MENU.parent.mkdir(parents=True, exist_ok=True)
            LINUX_MENU.write_text(_desktop_entry(cmd, autostart=False))
    except Exception as e:
        log.warning("Could not add launcher entry: %s", e)
