# Plane Overhead

Desktop app that watches live ADS-B traffic around you and notifies you **before** a low
plane flies over. It skips planes that will land at an airport before they reach you,
shows each aircraft with a shape matching its type (airliner, heavy, business jet,
turboprop, light plane, helicopter, military, glider, balloon, drone), and shows the full
aircraft name (e.g. "Boeing 737-800") in the side panel.

See [PROJECT.md](PROJECT.md) for goals, architecture and the task list.

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
