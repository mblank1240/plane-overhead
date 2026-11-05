# Plane Overhead — Project Goals & Tasks

## What it is
A desktop flight tracker that watches live ADS-B data (adsb.lol) around your location and
sends a desktop notification **before** a low-flying plane passes overhead. It also tracks
"favorite" aircraft (by tail number, flight number, or ICAO hex) and alerts when they take
off or come into range.

## Goals
1. **Accurate overhead alerts** — predict each plane's closest approach (time, miss distance,
   altitude above you) and alert `lead_time_sec` ahead when it will pass within
   `pass_distance_mi` below `max_altitude_agl_ft`.
2. **Don't cry wolf** — suppress alerts for planes that will land at an airport before they
   reach you.
3. **Readable map** — live map (Leaflet + OpenStreetMap) with plane markers whose shape
   reflects the aircraft type, a side panel with full aircraft names and details, the
   flight's trail and projected path.
4. **Native, cross-platform app** — Windows, macOS, Linux. Background process with a tray /
   menu-bar icon, native notifications (click to open the plane), and a native webview window.
5. **Stay off antivirus radar** — the app must not look like malware to Windows Defender or
   SentinelOne (see incident below).

## Architecture
| File | Role |
|---|---|
| `main.py` | Entry point. Background app (tracker + server + tray) or `--window` process |
| `tracker.py` | Polls adsb.lol, dead-reckons positions, computes closest approach, raises alerts |
| `server.py` | Local HTTP server on 127.0.0.1:8737 serving `web/` and a JSON API |
| `window.py` | pywebview window process; receives "focus this plane" commands |
| `notify.py` | Cross-platform notifications (desktop-notifier, with fallbacks) |
| `integration.py` | Start-at-login and launcher menu entries per OS |
| `paths.py` | Per-OS data dir and resource paths |
| `make_icon.py` | Generates `assets/icon.png` / `icon.ico` with stdlib only |
| `web/` | Map UI: `index.html`, `style.css`, `app.js` |

Dependencies (from the surviving venv): `pywebview`, `pystray`, `Pillow`, `desktop-notifier`
(and `pyinstaller` for builds). Settings live in `%APPDATA%\Plane Overhead` /
`~/Library/Application Support/Plane Overhead` / `~/.local/share/plane-overhead`.

## Incident: antivirus deleted the project (2026-11-05)
Windows Defender and SentinelOne quarantined/deleted files after the native-app work.
Lost: `web/index.html`, `web/style.css`, `web/icon.svg`, `assets/`, build scripts, and
Claude's memory. Surviving: all Python modules and `web/app.js`.

Likely triggers (classic malware heuristics, in combination):
- An unsigned **PyInstaller** executable (very commonly flagged).
- **Silently writing the HKCU `...\CurrentVersion\Run` key on first launch** (persistence).
- Spawning **hidden PowerShell** (`CREATE_NO_WINDOW`, `-NonInteractive -Command`) with the
  script's inputs smuggled through **environment variables** — used for the Start Menu
  shortcut and the toast fallback. This looks exactly like a living-off-the-land loader.

Rules going forward:
- No PowerShell / cmd / script-host spawning from the app.
- Start-at-login is **opt-in only** (user ticks the box), never automatic.
- Run from source (`python main.py`) during development; do not build a PyInstaller exe
  unless asked, and if built, use one-dir mode and code-sign it.

## Tasks
### Done (before the incident)
- [x] Tracker engine: polling, closest-approach prediction, overhead alerts
- [x] Favorites with takeoff / in-range alerts, quiet hours, pause
- [x] Map UI with trails, projected path, alert history, settings
- [x] Split into background app + native window, tray icon, notifications, autostart

### Now
- [x] Write this file; back up to a public GitHub repo
- [ ] Recreate the lost `web/index.html` and `web/style.css` (matching `app.js` IDs)
- [ ] Remove the AV triggers listed above
- [ ] Distinct marker shapes per aircraft type (airliner, widebody, regional/turboprop,
      light prop, helicopter, military/fighter, glider, balloon/blimp, drone, ground vehicle)
- [ ] Full aircraft type name in the side panel (e.g. "B738" → "Boeing 737-800")
- [ ] Airport-landing check: if an airport lies on the plane's path, the plane is descending
      toward it, and it would reach the airport before your location, skip the alert
- [ ] `requirements.txt`, README with run instructions

### Later
- [ ] Packaging per OS (signed builds) once AV-safe
- [ ] Regenerate `assets/` icons via `make_icon.py`
