"""Flight tracking engine: polls ADS-B data, predicts overhead passes, raises alerts."""

import json
import logging
import math
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


def norm(s):
    return (s or "").strip().upper().replace("-", "")


def parse_hhmm(s):
    h, m = (s or "0:0").split(":")
    return int(h) * 60 + int(m)


class Tracker:
    def __init__(self, data_dir, on_alert=None):
        """on_alert(alert, sound) is called for each alert that should notify the user."""
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.on_alert = on_alert
        self.settings_store = JsonStore(data_dir / "settings.json", {})
        self.favorites_store = JsonStore(data_dir / "favorites.json", [])
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

    def favorite_for(self, ac):
        hex_, reg, flight = ac.get("hex", ""), norm(ac.get("r")), norm(ac.get("flight"))
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

        p = {
            "hex": ac["hex"],
            "flight": (ac.get("flight") or "").strip(),
            "reg": ac.get("r") or "",
            "type": ac.get("t") or "",
            "desc": ac.get("desc") or "",
            "category": ac.get("category") or "",
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
        return p

    # ---- alerts ----------------------------------------------------------------

    def check_alerts(self, p, s, now):
        prev = self.seen.get(p["hex"])
        recently_seen = prev is not None and now - prev["t"] < REVISIT_SEC

        if (p["candidate"] and s["notify_overhead"]
                and p["pass"]["t"] <= s["lead_time_sec"] + s["poll_interval_sec"] / 2):
            self.raise_alert("overhead", p, s, now, cooldown=900)

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
        return (
            f"✈ {name} overhead in {eta // 60}:{eta % 60:02d}",
            f"{int(p['pass']['agl']):,} ft above you{trend}, passing within "
            f"{p['pass']['miss_mi']:.1f} mi. Now {p['dist_mi']:.0f} mi away at {int(p['gs'])} kt.",
        )
    if kind == "takeoff":
        return (
            f"⭐ {name} just took off",
            f"Climbing through {int(p['alt']):,} ft, {p['dist_mi']:.0f} mi from you.",
        )
    where = "on the ground" if p["on_ground"] else f"at {int(p['alt']):,} ft"
    return (f"⭐ {name} is nearby", f"{p['dist_mi']:.0f} mi from you, {where}.")
