#!/usr/bin/env python3
"""Build the static world base map for the analytics dashboard.

Projects world-countries.json (GeoJSON) to an equirectangular SVG, drops
Antarctica, and simplifies each polygon so the browser loads a small static
map instead of the 2.2 MB source. The email map (render_map_png) still reads
the vendor GeoJSON, so that file stays.

Run once, commit the output:  python scripts/build-world-map-svg.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "analytics_app" / "static" / "vendor" / "world-countries.json"
OUT = ROOT / "analytics_app" / "static" / "world-map.svg"

WIDTH, HEIGHT = 1000, 500
LAT_MIN, LAT_MAX = -60.0, 84.0  # crop the poles and Antarctica
TOLERANCE = 1.0  # projected-pixel tolerance for Douglas-Peucker
ISO_KEY = "ISO_A3_EH"


def project(lon: float, lat: float) -> tuple[float, float]:
    x = (lon + 180.0) / 360.0 * WIDTH
    lat = min(max(lat, LAT_MIN), LAT_MAX)
    y = (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * HEIGHT
    return x, y


def perp_distance(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return ((p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2) ** 0.5
    return abs(dy * p[0] - dx * p[1] + b[0] * a[1] - b[1] * a[0]) / (dx * dx + dy * dy) ** 0.5


def simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    a, b = points[0], points[-1]
    dmax, index = 0.0, 0
    for i in range(1, len(points) - 1):
        d = perp_distance(points[i], a, b)
        if d > dmax:
            dmax, index = d, i
    if dmax > tolerance:
        left = simplify(points[: index + 1], tolerance)
        right = simplify(points[index:], tolerance)
        return left[:-1] + right
    return [a, b]


def path_for_ring(ring) -> str:
    pts = [project(lon, lat) for lon, lat in ring]
    # drop out-of-band / degenerate rings after projecting
    pts = simplify(pts, TOLERANCE)
    if len(pts) < 3:
        return ""
    d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"
    for x, y in pts[1:]:
        d += f" L {x:.1f} {y:.1f}"
    d += " Z"
    return d


def rings_for(geometry) -> list[list]:
    if geometry["type"] == "Polygon":
        return geometry["coordinates"]
    if geometry["type"] == "MultiPolygon":
        return [ring for polygon in geometry["coordinates"] for ring in polygon]
    return []


def main() -> None:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    paths = []
    for feature in data["features"]:
        iso3 = feature.get("properties", {}).get(ISO_KEY)
        if not iso3 or iso3 == "ATA":
            continue
        rings = rings_for(feature["geometry"])
        d_parts = []
        for ring in rings:
            if not ring:
                continue
            lat = [pt[1] for pt in ring]
            if max(lat) < LAT_MIN:  # entirely south of the crop
                continue
            d = path_for_ring(ring)
            if d:
                d_parts.append(d)
        if d_parts:
            paths.append(f'    <path data-iso3="{iso3}" d="{" ".join(d_parts)}"/>')

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 500" '
        'role="img" aria-label="World map">\n'
        '  <g class="world-map">\n' + "\n".join(paths) + "\n  </g>\n</svg>\n"
    )
    OUT.write_text(svg, encoding="utf-8")
    print(f"wrote {OUT} with {len(paths)} countries, {len(svg.encode())} bytes")


if __name__ == "__main__":
    main()
