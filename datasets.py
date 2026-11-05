"""Reference data downloaded on first run and cached in the data dir.

- Aircraft types: ICAO type designator -> full name, engine type/count, wake class
  (tar1090-db, from the Mictronics database). Used for full names and map shapes.
- Airports: OurAirports (public domain). Used to spot planes landing before they reach you.

Both refresh every 30 days. Until a download succeeds the app still runs: names fall
back to the type code, shapes to the ADS-B category, and no alerts are suppressed.
"""

import csv
import gzip
import io
import json
import logging
import math
import re
import time
import urllib.request
from pathlib import Path

TYPES_URL = "https://raw.githubusercontent.com/wiedehopf/tar1090-db/master/db/icao_aircraft_types2.js"
AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
RUNWAYS_URL = "https://davidmegginson.github.io/ourairports-data/runways.csv"
USER_AGENT = "plane-overhead/2.1"
MAX_AGE_SEC = 30 * 86400
RETRY_SEC = 3600
AIRPORT_TYPES = {"small_airport", "medium_airport", "large_airport"}

log = logging.getLogger("datasets")


def _download(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


# ---- aircraft types ---------------------------------------------------------------

# Words in the database that should stay as written, not be title-cased.
KEEP_CASE = {"ATR", "BAE", "CASA", "PZL", "IAI", "MD", "AVIC", "HAL", "AMD", "EADS", "SOCATA",
             "CNIAR", "FFA", "MBB", "NAMC", "SIAI", "SAAB", "TL", "AISA", "AAI", "ICON", "DHC"}
SPECIAL_CASE = {"MCDONNELL": "McDonnell", "DE": "de", "HAVILLAND": "Havilland", "MCCAULEY": "McCauley"}
FIGHTER_NAME = re.compile(
    r"(\bF-(4|5|14|15|16|18|22|35|111|117)\b|F/?A-18|\bT-38|\bT-45|\bA-10\b|AV-8|EUROFIGHTER|TYPHOON|RAFALE|"
    r"GRIPEN|TORNADO|MIRAGE|MIG-|\bSU-\d|\bHAWK\b|ALPHA JET|L-39|HARRIER|JAGUAR|VIGGEN|\bB-[12]\b|"
    r"SR-71|\bU-2\b)", re.I)
GROUND_CODES = {"GND", "SERV", "EMER", "TWR"}
GLIDER_CODES = {"GLID", "GLIM", "ULAC", "PARA", "GYRO"}
BIZJET_NAME = re.compile(
    r"(GULFSTREAM|LEARJET|CITATION|FALCON|CHALLENGER|GLOBAL|PHENOM|LEGACY|PRAETOR|HONDAJET|HONDA|"
    r"PC-24|PREMIER|HAWKER|ECLIPSE|VISION|BEECHJET|SABRELINER|WESTWIND|ASTRA|LINEAGE|NEXTANT|"
    r"STRATOS|SYBERJET|EMB-505|EMB-500|G-?IV|G-?V)", re.I)


def pretty_name(raw):
    """'BOEING 737-800' -> 'Boeing 737-800', 'AIRBUS A-330-300' -> 'Airbus A330-300'."""
    def fix(w):
        if w in SPECIAL_CASE:
            return SPECIAL_CASE[w]
        if w.isalpha() and w.isupper() and w not in KEEP_CASE and len(w) > 2:
            return w.capitalize()
        return w
    # Hyphenated maker names like MCDONNELL-DOUGLAS are fixed part by part;
    # model numbers like DHC-8-400 are left alone.
    name = " ".join("-".join(fix(part) for part in w.split("-")) if re.fullmatch(r"[A-Z]+(-[A-Z]+)+", w)
                    else fix(w) for w in raw.split())
    return re.sub(r"\bA-(\d{3})", r"A\1", name)  # Airbus writes A330, not A-330


def classify(code, name, desc, wtc):
    """Pick a map shape from the ICAO description (e.g. 'L2J' = land plane, 2 jets)."""
    if code in GROUND_CODES:
        return "ground"
    if code in GLIDER_CODES:
        return "glider"
    kind = desc[:1]
    engines = desc[1:2]
    engine = desc[2:3]
    if kind in ("H", "G", "T"):
        return "heli"
    if kind == "B":
        return "balloon"
    if kind == "D":
        return "drone"
    if engine == "J":
        if FIGHTER_NAME.search(name):
            return "fighter"
        if wtc in ("H", "J"):
            return "heavy"
        if BIZJET_NAME.search(name) or wtc == "L":
            return "bizjet"
        return "jet"
    if engine == "-" or engines == "0":
        return "glider"
    if engine in ("T", "P", "E"):
        return "light" if engines == "1" else "turboprop"
    return None


class AircraftTypes:
    def __init__(self, path):
        self.path = Path(path)
        self.types = {}  # code -> {"name", "shape", "engines"}
        self.loaded_at = 0

    def load_cached(self):
        if self.path.exists():
            try:
                self.types = json.loads(self.path.read_text(encoding="utf-8"))
                self.loaded_at = self.path.stat().st_mtime
            except (OSError, ValueError) as e:
                log.warning("Aircraft types cache unreadable (%s)", e)

    def refresh(self):
        raw = json.loads(_download(TYPES_URL))
        out = {}
        for code, row in raw.items():
            name, desc, wtc = (list(row) + ["", "", ""])[:3]
            out[code] = {"name": pretty_name(name) if name else "",
                         "shape": classify(code, name or "", desc or "", wtc or "")}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.path)
        self.types = out
        self.loaded_at = time.time()
        log.info("Loaded %d aircraft types", len(out))

    def get(self, code):
        return self.types.get((code or "").upper())


# ---- airports -----------------------------------------------------------------------

class Airports:
    def __init__(self, path):
        self.path = Path(path)
        self.rows = []       # [ident, name, lat, lon, elev_ft, kind, [runway axes 0-179 deg true]]
        self.loaded_at = 0
        self._near_key = None
        self._near = []

    def load_cached(self):
        if self.path.exists():
            try:
                self.rows = json.loads(self.path.read_text(encoding="utf-8"))
                self.loaded_at = self.path.stat().st_mtime
                self._near_key = None
            except (OSError, ValueError) as e:
                log.warning("Airports cache unreadable (%s)", e)

    def refresh(self):
        axes = runway_axes(_download(RUNWAYS_URL).decode("utf-8-sig"))
        text = _download(AIRPORTS_URL).decode("utf-8-sig")
        rows = []
        for r in csv.DictReader(io.StringIO(text)):
            if r["type"] not in AIRPORT_TYPES:
                continue
            try:
                lat, lon = float(r["latitude_deg"]), float(r["longitude_deg"])
            except ValueError:
                continue
            elev = float(r["elevation_ft"]) if r["elevation_ft"] else 0.0
            ident = r.get("icao_code") or r.get("gps_code") or r["iata_code"] or r["ident"]
            rows.append([ident, r["name"], round(lat, 5), round(lon, 5), round(elev),
                         r["type"].split("_")[0], sorted(axes.get(r["ident"], ()))])
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.path)
        self.rows = rows
        self.loaded_at = time.time()
        self._near_key = None
        log.info("Loaded %d airports", len(rows))

    def near(self, lat, lon, radius_nm):
        """Airports within radius_nm of (lat, lon), with x/y in nm relative to that point."""
        key = (round(lat, 3), round(lon, 3), round(radius_nm), len(self.rows))
        if key != self._near_key:
            cos_lat = math.cos(math.radians(lat))
            near = []
            for ident, name, alat, alon, elev, kind, *rest in self.rows:
                y = (alat - lat) * 60.0
                if abs(y) > radius_nm:
                    continue
                x = (alon - lon) * 60.0 * cos_lat
                if math.hypot(x, y) <= radius_nm:
                    near.append({"ident": ident, "name": name, "lat": alat, "lon": alon,
                                 "elev": elev, "kind": kind, "runways": rest[0] if rest else [],
                                 "x": x, "y": y})
            self._near, self._near_key = near, key
        return self._near


def _bearing(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def runway_axes(text):
    """airport ident -> set of open runway directions, as true headings folded into 0-179."""
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        if r["closed"] == "1":
            continue
        heading = None
        try:
            if r["le_heading_degT"]:
                heading = float(r["le_heading_degT"])
            elif r["le_latitude_deg"] and r["he_latitude_deg"]:
                heading = _bearing(float(r["le_latitude_deg"]), float(r["le_longitude_deg"]),
                                   float(r["he_latitude_deg"]), float(r["he_longitude_deg"]))
            else:
                m = re.match(r"(\d{1,2})", r["le_ident"] or "")
                if m and 1 <= int(m.group(1)) <= 36:
                    heading = int(m.group(1)) * 10.0  # magnetic, but close enough
        except ValueError:
            pass
        if heading is not None:
            out.setdefault(r["airport_ident"], set()).add(round(heading) % 180)
    return out


# ---- background loader ---------------------------------------------------------------

class Datasets:
    def __init__(self, data_dir):
        data_dir = Path(data_dir)
        self.types = AircraftTypes(data_dir / "aircraft_types.json")
        self.airports = Airports(data_dir / "airports.json")
        for d in (self.types, self.airports):
            d.load_cached()

    def run_forever(self):
        while True:
            for d in (self.types, self.airports):
                if time.time() - d.loaded_at > MAX_AGE_SEC:
                    try:
                        d.refresh()
                    except Exception as e:
                        log.warning("Could not download %s (%s); will retry", d.path.name, e)
            time.sleep(RETRY_SEC)
