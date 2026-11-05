"""Draws the app icon (blue disc with a white plane) as icon.ico and icon.png, using only the stdlib."""

import math
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLUE = (37, 99, 235)
WHITE = (255, 255, 255)

# The plane from web/icon.svg, flattened to a polygon on a 24x24 grid, nose up.
PLANE = [(21, 14), (13, 9), (13, 3.5), (12.6, 2.4), (11.5, 2), (10.4, 2.4), (10, 3.5), (10, 9),
         (2, 14), (2, 16), (10, 13.5), (10, 19), (8, 20.5), (8, 22), (11.5, 21), (15, 22),
         (15, 20.5), (13, 19), (13, 13.5), (21, 16)]


def plane_polygon(size):
    a = math.radians(45)
    scale = size / 24 * 0.78
    pts = []
    for x, y in PLANE:
        x, y = x - 11.5, y - 12
        pts.append((size / 2 + (x * math.cos(a) - y * math.sin(a)) * scale,
                    size / 2 + (x * math.sin(a) + y * math.cos(a)) * scale))
    return pts


def inside(px, py, poly):
    hit = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def render(size, ss=3):
    poly = plane_polygon(size)
    r = size / 2 - 0.5
    rows = []
    for y in range(size):
        row = bytearray([0])  # PNG filter byte
        for x in range(size):
            disc = plane = 0
            for sy in range(ss):
                for sx in range(ss):
                    px, py = x + (sx + 0.5) / ss, y + (sy + 0.5) / ss
                    if math.hypot(px - size / 2, py - size / 2) <= r:
                        disc += 1
                        if inside(px, py, poly):
                            plane += 1
            n = ss * ss
            if disc == 0:
                row += bytes(4)
                continue
            t = plane / disc
            rgb = [round(BLUE[c] * (1 - t) + WHITE[c] * t) for c in range(3)]
            row += bytes(rgb + [round(255 * disc / n)])
        rows.append(bytes(row))
    return png(size, b"".join(rows))


def png(size, raw):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def ico(images):
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    return header + entries + blobs


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "assets"
    out.mkdir(exist_ok=True)
    images = [(s, render(s, 2 if s >= 128 else 4)) for s in (256, 48, 32, 16)]
    (out / "icon.ico").write_bytes(ico(images))
    (out / "icon.png").write_bytes(images[0][1])
    print("Wrote icon.ico and icon.png")
