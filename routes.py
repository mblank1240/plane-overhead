"""Flight routes (origin / destination) looked up by callsign.

Uses adsb.im's routeset API (the service behind adsb.lol's /api/0/routeset, which
currently returns empty replies). The data is the published route for a flight
number, not a filed flight plan, so private flights (callsign = tail number) have
none. "plausible" says whether the plane's current position fits the route.
"""

import json
import logging
import re
import threading
import time
import urllib.request

ROUTESET_URL = "https://adsb.im/api/0/routeset"
USER_AGENT = "plane-overhead/2.1"
HIT_TTL_SEC = 6 * 3600
MISS_TTL_SEC = 3600
ERROR_TTL_SEC = 120
CALLSIGN = re.compile(r"[A-Z0-9]{2,8}")

log = logging.getLogger("routes")


class Routes:
    def __init__(self):
        self.cache = {}  # callsign -> (expires, result)
        self.lock = threading.Lock()

    def lookup(self, callsign, lat, lon):
        """{"callsign", "found", "plausible", "airports": [{icao, iata, name, city, lat, lon}]}"""
        callsign = (callsign or "").strip().upper()
        if not CALLSIGN.fullmatch(callsign):
            raise ValueError("Not a valid callsign")
        with self.lock:
            hit = self.cache.get(callsign)
            if hit and hit[0] > time.time():
                return hit[1]
        try:
            result = self._fetch(callsign, float(lat), float(lon))
            ttl = HIT_TTL_SEC if result["found"] else MISS_TTL_SEC
        except (OSError, ValueError) as e:
            log.warning("Route lookup for %s failed: %s", callsign, e)
            result = {"callsign": callsign, "found": False, "error": "Route service unavailable",
                      "airports": [], "plausible": False}
            ttl = ERROR_TTL_SEC
        with self.lock:
            self.cache[callsign] = (time.time() + ttl, result)
            if len(self.cache) > 2000:
                now = time.time()
                self.cache = {k: v for k, v in self.cache.items() if v[0] > now}
        return result

    def _fetch(self, callsign, lat, lon):
        body = json.dumps({"planes": [{"callsign": callsign, "lat": lat, "lng": lon}]}).encode()
        req = urllib.request.Request(ROUTESET_URL, data=body, headers={
            "User-Agent": USER_AGENT, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.load(r)
        row = next((x for x in rows if x.get("callsign", "").upper() == callsign), None) or {}
        airports = [{
            "icao": a.get("icao") or "",
            "iata": a.get("iata") or "",
            "name": a.get("name") or "",
            "city": a.get("location") or "",
            "lat": a.get("lat"),
            "lon": a.get("lon"),
        } for a in row.get("_airports") or []]
        return {"callsign": callsign, "found": len(airports) >= 2,
                "plausible": bool(row.get("plausible")), "airports": airports}
