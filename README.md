# Plane Overhead

Desktop app that watches live ADS-B traffic around you and notifies you **before** a low
plane flies over. It skips planes that will land at an airport before they reach you,
shows each aircraft with a shape matching its type (airliner, heavy, business jet,
turboprop, light plane, helicopter, military, glider, balloon, drone), and shows the full
aircraft name (e.g. "Boeing 737-800") in the side panel.

See [PROJECT.md](PROJECT.md) for goals, architecture and the task list.

## Download

Grab the zip for your OS from the [Releases page](https://github.com/mblank1240/plane-overhead/releases).
The builds are **not code-signed**, so the OS will warn you the first time:

- **Windows:** unzip, run `PlaneOverhead\PlaneOverhead.exe`. On "Windows protected your PC",
  click **More info → Run anyway**.
- **macOS (Apple Silicon):** unzip, move **Plane Overhead** to Applications, then right-click it →
  **Open** → **Open**. If macOS says it's damaged or can't be opened, go to System Settings →
  Privacy & Security and click **Open Anyway**.
- **Linux (x64):** `tar -xzf PlaneOverhead-linux-x64.tar.gz && ./PlaneOverhead/PlaneOverhead`.
  The map opens in your browser. The tray icon needs AppIndicator support (on GNOME, the
  "AppIndicator" extension).

## Making a release

Builds run on GitHub Actions (`.github/workflows/build.yml`) for Windows, macOS and Linux:

```sh
git tag v2.1.0 && git push origin v2.1.0   # builds all three and publishes a Release
```

Or use **Actions → Build → Run workflow** to build without releasing (downloads appear on the run page).
To build locally: `pip install pyinstaller && pyinstaller plane-overhead.spec` (output in `dist/`).
Regenerate icons with `python tools/make_icon.py`.

## Run from source

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Open **Settings**, click **Pick on map** to set your location, and you're done.

## Data sources
- Flight data: [adsb.lol](https://adsb.lol) (free, no key)
- Aircraft types: [tar1090-db](https://github.com/wiedehopf/tar1090-db) / Mictronics, downloaded on first run
- Airports and runways: [OurAirports](https://ourairports.com/data/) (public domain), downloaded on first run
- Map: OpenStreetMap via Leaflet
