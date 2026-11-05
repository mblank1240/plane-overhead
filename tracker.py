"""Flight tracking engine: polls ADS-B data, predicts overhead passes, raises alerts."""

import json
import logging
import math
import re
import threading
import time
import urllib.request
import uuid
from collections import deque
from pathlib import Path

API_URL = "https://api.adsb.lol/v2/point/{lat:.5f}/{lon:.5f}/{radius}"
NM_PER_DEG_LAT = 60.0
MI_PER_NM = 1.150779
MAX_GS_KT = 550          # fastest plane we expect at low altitude, sizes the search radius
TRAIL_POINTS = 360       # an hour of history at 10s polls
FORGET_AFTER_SEC = 3600  # drop planes (and their trails) not seen for an hour
REVISIT_SEC = 600        # a plane unseen this long counts as newly appeared
TAKEOFF_MAX_AGL_FT = 2500

# Landing check: an airport counts as "in the path" if the projected track passes within
# this many nm of it, and the plane is "landing" there if it is descending and would be
# near the ground by the time it arrives.
APPROACH_CORRIDOR_NM = 1.5
APPROACH_MAX_NM = 30
LANDING_MAX_ARRIVAL_AGL_FT = 1000   # projected height over the field on arrival
LANDING_MIN_DESCENT_FPM = 200
LANDING_MAX_CLIMB_FPM = 500         # climbing faster than this = taking off / going around
GLIDEPATH_FT_PER_NM = 350           # a 3-degree glideslope is ~320 ft per nm
GLIDEPATH_MARGIN_FT = 1500          # room for level segments and traffic patterns
RUNWAY_ALIGN_DEG = 15
ALIGNED_MAX_NM = 10                 # runway alignment only counts this close in...
ALIGNED_FT_PER_NM = 320             # ...and on or under a 3-degree glidepath
ALIGNED_MARGIN_FT = 800
ALIGNED_MAX_CLIMB_FPM = 300
PATTERN_NM = 3                      # close-in, for airports with no runway headings
PATTERN_MAX_AGL_FT = 1500

SHAPES = {"jet", "heavy", "bizjet", "turboprop", "light", "heli", "fighter", "glider",
          "balloon", "drone", "ground"}

# Map shape from the ADS-B emitter category, used when the type code is unknown.
CATEGORY_SHAPES = {
    "A1": "light", "A2": "bizjet", "A3": "jet", "A4": "jet", "A5": "heavy", "A6": "fighter",
    "A7": "heli", "B1": "glider", "B2": "balloon", "B3": "glider", "B4": "light", "B6": "drone",
    "C1": "ground", "C2": "ground", "C3": "ground",
}

DEFAULT_SETTINGS = {
    "latitude": None,
    "longitude": None,
    "elevation_ft": 0,
    "notify_overhead": True,
    "max_altitude_agl_ft": 8000,
    "pass_distance_mi": 1.0,
    "lead_time_sec": 180,
    "poll_interval_sec": 10,
    "sound": True,
    "quiet_hours_enabled": False,
    "quiet_start": "18:00",
    "quiet_end": "07:00",
    "favorite_radius_mi": 30,
    "favorite_notify_takeoff": True,
    "favorite_notify_in_range": False,
    "skip_landing": True,
}

log = logging.getLogger("tracker")


class JsonStore:
    """A JSON file holding one value, read once and rewritten atomically on save."""

    def __init__(self, path, default):
        self.path = Path(path)
        self.value = default
        if self.path.exists():
            try:
                self.value = json.loads(self.path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as e:
                log.warning("Could not read %s (%s); starting fresh", self.path, e)

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.value, indent=2), encoding="utf-8")
        tmp.replace(self.path)


US_TAIL = re.compile(r"N[1-9][0-9A-Z]{0,4}")


def registration(ac):
    """The tail number, or the callsign when it is a US tail number and no registration came."""
    reg = (ac.get("r") or "").strip()
    if reg:
        return reg
    callsign = (ac.get("flight") or "").strip().upper()
    return callsign if US_TAIL.fullmatch(callsign) else ""


def norm(s):
    return (s or "").strip().upper().replace("-", "")


def parse_hhmm(s):
    h, m = (s or "0:0").split(":")
    return int(h) * 60 + int(m)


class Tracker:
    def __init__(self, data_dir, on_alert=None, datasets=None):
        """on_alert(alert, sound) is called for each alert that should notify the user.

        datasets (datasets.Datasets) supplies aircraft type names and airports; without
        it, names fall back to type codes and no alerts are suppressed for landings.
        """
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.on_alert = on_alert
        self.datasets = datasets
        self.settings_store = JsonStore(data_dir / "settings.json", {})
        self.favorites_store = JsonStore(data_dir / "favorites.json", [])
        # Names you gave aircraft the type database doesn't know, keyed by tail number
        # (or "hex:<icao>" for aircraft that broadcast no registration).
        self.custom_types_store = JsonStore(data_dir / "custom_types.json", {})
        self.alerts_store = JsonStore(data_dir / "alerts.json", [])
        self.lock = threading.RLock()
        self.wake = threading.Event()

        self.aircraft = {}   # hex -> latest processed aircraft dict
        self.trails = {}     # hex -> deque of [t, lat, lon, alt]
        self.seen = {}       # hex -> {"t": last seen, "ground": bool}
        self.notified = {}   # (kind, hex) -> time, to alert once per pass
        self.snapshot_ts = 0
        self.last_error = None
        self.last_ok = 0
        self.paused_until = 0

    # ---- settings / favorites ------------------------------------------------

    @property
    def settings(self):
        return {**DEFAULT_SETTINGS, **self.settings_store.value}

    def update_settings(self, changes):
        with self.lock:
            clean = {k: v for k, v in changes.items() if k in DEFAULT_SETTINGS}
            self.settings_store.value = {**self.settings_store.value, **clean}
            self.settings_store.save()
        self.wake.set()  # re-poll right away with the new location/radius

    @property
    def favorites(self):
        return self.favorites_store.value

    def add_favorite(self, fav):
        kind = fav.get("kind")
        value = norm(fav.get("value"))
        if kind not in ("hex", "reg", "flight") or not value:
            raise ValueError("Favorite needs kind (hex, reg or flight) and a value")
        with self.lock:
            for f in self.favorites:
                if f["kind"] == kind and f["value"] == value:
                    return f
            entry = {
                "id": uuid.uuid4().hex[:10],
                "kind": kind,
                "value": value.lower() if kind == "hex" else value,
                "label": (fav.get("label") or "").strip(),
                "type": fav.get("type") or "",
                "added": time.time(),
                "last_seen": None,
            }
            self.favorites.append(entry)
            self.favorites_store.save()
            return entry

    def remove_favorite(self, fav_id):
        with self.lock:
            self.favorites_store.value = [f for f in self.favorites if f["id"] != fav_id]
            self.favorites_store.save()

    # ---- names you gave aircraft ------------------------------------------------

    @staticmethod
    def custom_key(reg, hex_):
        reg = norm(reg)
        return reg if reg else f"hex:{(hex_ or '').strip().lower()}"

    def custom_type_for(self, ac):
        store = self.custom_types_store.value
        reg = norm(registration(ac))
        return (store.get(reg) if reg else None) or store.get(f"hex:{ac.get('hex', '')}")

    def set_custom_type(self, data):
        name = (data.get("name") or "").strip()[:80]
        code = norm(data.get("type"))[:6]
        shape = data.get("shape") or ""
        if not (data.get("reg") or data.get("hex")):
            raise ValueError("Need the aircraft's tail number or ICAO hex")
        if not name and not code:
            raise ValueError("Enter a name or an ICAO type code")
        if shape and shape not in SHAPES:
            raise ValueError(f"Unknown shape {shape!r}")
        key = self.custom_key(data.get("reg"), data.get("hex"))
        entry = {"name": name, "type": code, "shape": shape,
                 "reg": norm(data.get("reg")), "hex": (data.get("hex") or "").lower(), "added": time.time()}
        with self.lock:
            self.custom_types_store.value[key] = entry
            self.custom_types_store.save()
        self.wake.set()  # re-poll so the new name shows right away
        return entry

    def remove_custom_type(self, data):
        key = self.custom_key(data.get("reg"), data.get("hex"))
        with self.lock:
            self.custom_types_store.value.pop(key, None)
            self.custom_types_store.save()
        self.wake.set()

    def favorite_for(self, ac):
        hex_, reg, flight = ac.get("hex", ""), norm(registration(ac)), norm(ac.get("flight"))
        for f in self.favorites:
            if ((f["kind"] == "hex" and f["value"] == hex_)
                    or (f["kind"] == "reg" and reg and f["value"] == reg)
                    or (f["kind"] == "flight" and flight and f["value"] == flight)):
                return f
        return None

    # ---- polling ---------------------------------------------------------------

    def search_radius_nm(self, s):
        overhead = s["lead_time_sec"] * MAX_GS_KT / 3600 + 5
        favorites = s["favorite_radius_mi"] / MI_PER_NM if self.favorites else 0
        return int(min(250, max(overhead, favorites, 10)) + 1)

    def fetch(self, s):
        url = API_URL.format(lat=s["latitude"], lon=s["longitude"], radius=self.search_radius_nm(s))
        req = urllib.request.Request(url, headers={"User-Agent": "plane-overhead/2.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp).get("ac", [])

    def run_forever(self):
        while True:
            s = self.settings
            if s["latitude"] is None or s["longitude"] is None:
                self.last_error = "Set your office location in Settings"
            else:
                try:
                    raw = self.fetch(s)
                    self.process(raw, s, time.time())
                    self.last_error = None
                    self.last_ok = time.time()
                except Exception as e:
                    self.last_error = f"Flight data unavailable: {e}"
                    log.warning(self.last_error)
            self.wake.wait(max(5, s["poll_interval_sec"]))
            self.wake.clear()

    def process(self, raw, s, now):
        with self.lock:
            current = {}
            for ac in raw:
                p = self.analyze(ac, s)
                if p:
                    current[p["hex"]] = p
                    self.check_alerts(p, s, now)
                    self.seen[p["hex"]] = {"t": now, "ground": p["on_ground"]}
                    trail = self.trails.setdefault(p["hex"], deque(maxlen=TRAIL_POINTS))
                    if not trail or trail[-1][1:3] != [p["lat"], p["lon"]]:
                        trail.append([round(now - p["age"], 1), p["lat"], p["lon"], p["alt"]])
            self.aircraft = current
            self.snapshot_ts = now
            if any(p["favorite"] for p in current.values()):
                self.favorites_store.save()  # persist updated last_seen
            for h in [h for h, v in self.seen.items() if now - v["t"] > FORGET_AFTER_SEC]:
                self.seen.pop(h, None)
                self.trails.pop(h, None)
            for k in [k for k, t in self.notified.items() if now - t > FORGET_AFTER_SEC]:
                del self.notified[k]

    def analyze(self, ac, s):
        """Turn a raw ADS-B record into position, velocity and closest-approach prediction.

        Uses a flat-earth projection around the office (accurate to well under 1%
        at these distances) and assumes the plane holds its current ground track,
        speed and vertical rate.
        """
        if ac.get("lat") is None or ac.get("lon") is None or not ac.get("hex"):
            return None
        on_ground = ac.get("alt_baro") == "ground"
        alt = 0 if on_ground else (ac.get("alt_geom") or ac.get("alt_baro"))
        if not isinstance(alt, (int, float)):
            return None

        cos_lat = math.cos(math.radians(s["latitude"]))
        x = (ac["lon"] - s["longitude"]) * NM_PER_DEG_LAT * cos_lat
        y = (ac["lat"] - s["latitude"]) * NM_PER_DEG_LAT
        gs = ac.get("gs") or 0
        track = ac.get("track")
        rate = ac.get("geom_rate") or ac.get("baro_rate") or 0
        age = ac.get("seen_pos") or 0
        fav = self.favorite_for(ac)
        custom = self.custom_type_for(ac)
        type_code = (ac.get("t") or (custom or {}).get("type") or "").upper()
        info = self.datasets.types.get(type_code) if self.datasets else None
        category = ac.get("category") or ""
        shape = ((custom or {}).get("shape") or (info or {}).get("shape")
                 or CATEGORY_SHAPES.get(category) or "jet")
        if on_ground and category.startswith("C"):
            shape = "ground"

        p = {
            "hex": ac["hex"],
            "flight": (ac.get("flight") or "").strip(),
            "reg": registration(ac),
            "type": type_code,
            "desc": ac.get("desc") or "",
            "type_name": ((custom or {}).get("name") or (info or {}).get("name")
                          or (ac.get("desc") or "").title()),
            "custom_type": custom,
            "shape": shape,
            "operator": ac.get("ownOp") or "",
            "category": category,
            "squawk": ac.get("squawk") or "",
            "lat": ac["lat"],
            "lon": ac["lon"],
            "alt": alt,
            "agl": None if on_ground else alt - s["elevation_ft"],
            "gs": gs,
            "track": track,
            "rate": rate,
            "on_ground": on_ground,
            "age": age,
            "dist_mi": math.hypot(x, y) * MI_PER_NM,
            "favorite": fav["id"] if fav else None,
            "pass": None,
            "landing": None,
            "candidate": False,
        }
        p["name"] = p["flight"] or p["reg"] or p["hex"].upper()

        if on_ground or track is None or gs < 30:
            return p
        speed = gs / 3600.0  # nm per second
        trk = math.radians(track)
        vx, vy = speed * math.sin(trk), speed * math.cos(trk)
        x, y = x + vx * age, y + vy * age  # bring a stale position up to "now"
        t_cpa = max(0.0, -(x * vx + y * vy) / (vx * vx + vy * vy))
        cx, cy = x + vx * t_cpa, y + vy * t_cpa
        agl_at_pass = alt + rate * t_cpa / 60.0 - s["elevation_ft"]
        p["pass"] = {
            "t": t_cpa,
            "miss_mi": math.hypot(cx, cy) * MI_PER_NM,
            "agl": agl_at_pass,
            "lat": s["latitude"] + cy / NM_PER_DEG_LAT,
            "lon": s["longitude"] + cx / (NM_PER_DEG_LAT * cos_lat),
        }
        p["candidate"] = (
            t_cpa > 5
            and p["pass"]["miss_mi"] <= s["pass_distance_mi"]
            and 0 < agl_at_pass <= s["max_altitude_agl_ft"]
        )
        if p["candidate"]:
            p["landing"] = self.landing_before_pass(x, y, vx, vy, track, alt, rate, t_cpa, s)
            if p["landing"] and s["skip_landing"]:
                p["candidate"] = False
        return p

    def landing_before_pass(self, x, y, vx, vy, track, alt, rate, t_cpa, s):
        """The airport this plane is landing at before it reaches you, or None.

        Candidates are airports on the projected track (within APPROACH_CORRIDOR_NM)
        that the plane reaches before its closest approach to you, with the plane low
        enough to be on approach (under a generous glidepath) and not climbing. It
        counts as landing there if any of these hold:
          - it is descending and would be near the field when it gets there
          - it is close in, on or under a 3-degree glidepath, and lined up with a runway
          - it is within a few miles at traffic-pattern height (airports with no runway data)
        """
        if not self.datasets or rate > LANDING_MAX_CLIMB_FPM:
            return None
        speed = math.hypot(vx, vy)
        ux, uy = vx / speed, vy / speed
        along_you = t_cpa * speed
        radius = self.search_radius_nm(s) + APPROACH_MAX_NM  # fixed per settings, so it caches
        axis = track % 180
        best = None
        for a in self.datasets.airports.near(s["latitude"], s["longitude"], radius):
            rx, ry = a["x"] - x, a["y"] - y
            along = rx * ux + ry * uy
            if not (0 < along < along_you and along <= APPROACH_MAX_NM):
                continue
            if abs(rx * uy - ry * ux) > APPROACH_CORRIDOR_NM:
                continue
            height = alt - a["elev"]
            if not (-300 < height <= GLIDEPATH_FT_PER_NM * along + GLIDEPATH_MARGIN_FT):
                continue
            t_arrive = along / speed
            descending = (rate <= -LANDING_MIN_DESCENT_FPM
                          and height + rate * t_arrive / 60.0 <= LANDING_MAX_ARRIVAL_AGL_FT)
            aligned = (along <= ALIGNED_MAX_NM and rate <= ALIGNED_MAX_CLIMB_FPM
                       and height <= ALIGNED_FT_PER_NM * along + ALIGNED_MARGIN_FT
                       and any(min(abs(axis - r), 180 - abs(axis - r)) <= RUNWAY_ALIGN_DEG
                               for r in a["runways"]))
            pattern = not a["runways"] and along <= PATTERN_NM and height <= PATTERN_MAX_AGL_FT
            if (descending or aligned or pattern) and (best is None or along < best["dist_nm"]):
                best = {"ident": a["ident"], "name": a["name"], "lat": a["lat"], "lon": a["lon"],
                        "dist_nm": round(along, 1), "t": t_arrive,
                        "why": "descending" if descending else "aligned" if aligned else "pattern"}
        return best

    # ---- alerts ----------------------------------------------------------------

    def check_alerts(self, p, s, now):
        prev = self.seen.get(p["hex"])
        recently_seen = prev is not None and now - prev["t"] < REVISIT_SEC

        due = p["pass"] and p["pass"]["t"] <= s["lead_time_sec"] + s["poll_interval_sec"] / 2
        if p["candidate"] and s["notify_overhead"] and due:
            self.raise_alert("overhead", p, s, now, cooldown=900)
        elif p["landing"] and due and ("landing", p["hex"]) not in self.notified:
            self.notified[("landing", p["hex"])] = now
            log.info("Skipped alert: %s is landing at %s, %.1f nm ahead of it, before reaching you",
                     p["name"], p["landing"]["ident"], p["landing"]["dist_nm"])

        if not p["favorite"]:
            return
        fav = next((f for f in self.favorites if f["id"] == p["favorite"]), None)
        if fav:
            fav["last_seen"] = {"t": now, "lat": p["lat"], "lon": p["lon"],
                                "name": p["name"], "on_ground": p["on_ground"]}
        if p["dist_mi"] > s["favorite_radius_mi"]:
            return
        # A takeoff is a ground-to-air transition we saw, or a plane that pops up
        # low and climbing (receivers often can't hear planes still on the runway).
        took_off = not p["on_ground"] and (
            (recently_seen and prev["ground"])
            or (not recently_seen and p["agl"] is not None
                and p["agl"] < TAKEOFF_MAX_AGL_FT and p["rate"] > 250)
        )
        if took_off and s["favorite_notify_takeoff"]:
            self.raise_alert("takeoff", p, s, now, cooldown=1800)
        elif not recently_seen and s["favorite_notify_in_range"]:
            self.raise_alert("in_range", p, s, now, cooldown=3600)

    def raise_alert(self, kind, p, s, now, cooldown):
        key = (kind, p["hex"])
        last = self.notified.get(key)
        if last is not None and now - last < cooldown:
            return
        self.notified[key] = now
        title, body = describe(kind, p)
        alert = {
            "id": uuid.uuid4().hex[:10],
            "kind": kind,
            "t": now,
            "hex": p["hex"],
            "name": p["name"],
            "reg": p["reg"],
            "type": p["type"],
            "title": title,
            "body": body,
            "eta": now + p["pass"]["t"] if kind == "overhead" else None,
        }
        self.alerts_store.value = ([alert] + self.alerts_store.value)[:200]
        self.alerts_store.save()
        log.info("ALERT %s | %s", title, body)
        if self.on_alert and not self.quiet_now(s) and not self.paused():
            self.on_alert(alert, s["sound"])

    def paused(self):
        return time.time() < self.paused_until

    def pause(self, minutes):
        self.paused_until = time.time() + minutes * 60 if minutes else 0

    def quiet_now(self, s):
        if not s["quiet_hours_enabled"]:
            return False
        t = time.localtime()
        m = t.tm_hour * 60 + t.tm_min
        start, end = parse_hhmm(s["quiet_start"]), parse_hhmm(s["quiet_end"])
        return (start <= m or m < end) if start > end else (start <= m < end)

    # ---- API views ---------------------------------------------------------------

    def state(self, trail_hex=None):
        with self.lock:
            s = self.settings
            planes = list(self.aircraft.values())
            upcoming = sorted(
                (p for p in planes if p["candidate"] and p["pass"]["t"] <= 900),
                key=lambda p: p["pass"]["t"],
            )
            out = {
                "ts": self.snapshot_ts,
                "now": time.time(),
                "ok": self.last_ok,
                "error": self.last_error,
                "settings": s,
                "aircraft": planes,
                "upcoming": [p["hex"] for p in upcoming],
                "alerts": self.alerts_store.value[:50],
                "favorites": self.favorites,
                "quiet": self.quiet_now(s),
                "paused_until": self.paused_until if self.paused() else None,
            }
            if trail_hex and trail_hex in self.trails:
                out["trail"] = {"hex": trail_hex, "points": list(self.trails[trail_hex])}
            return out


def describe(kind, p):
    name = p["name"] + (f" ({p['type']})" if p["type"] else "")
    if kind == "overhead":
        eta = int(round(p["pass"]["t"]))
        trend = " and climbing" if p["rate"] > 300 else " and descending" if p["rate"] < -300 else ""
        what = f"{p['type_name']}, " if p["type_name"] else ""
        return (
            f"✈ {name} overhead in {eta // 60}:{eta % 60:02d}",
            f"{what}{int(p['pass']['agl']):,} ft above you{trend}, passing within "
            f"{p['pass']['miss_mi']:.1f} mi. Now {p['dist_mi']:.0f} mi away at {int(p['gs'])} kt.",
        )
    if kind == "takeoff":
        return (
            f"⭐ {name} just took off",
            f"Climbing through {int(p['alt']):,} ft, {p['dist_mi']:.0f} mi from you.",
        )
    where = "on the ground" if p["on_ground"] else f"at {int(p['alt']):,} ft"
    return (f"⭐ {name} is nearby", f"{p['dist_mi']:.0f} mi from you, {where}.")
