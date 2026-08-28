"""Server-side WASP usage map rendering with matplotlib (Agg backend).

Projects world-countries.json to an equirectangular view (lat cropped to
-60..84 to drop Antarctica) and fills each country by a metric value on a
perceptual teal scale with clear white borders. Produces a static PNG used by
both the dashboard (via /api/v1/wasp/map.png) and the monthly email.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.cm import ScalarMappable
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Polygon

GEOJSON_PATH = Path(__file__).with_name("static") / "vendor" / "world-countries.json"

WIDTH, HEIGHT = 1000, 500
LAT_MIN, LAT_MAX = -60.0, 84.0
TOLERANCE = 1.0
ISO_KEY = "ISO_A3_EH"

TEAL_CMAP = LinearSegmentedColormap.from_list(
    "usage_teal", ["#f0faf7", "#c8ebe1", "#8cd3c2", "#3da48f", "#0b4f4a"]
)
MAP_METRICS = ("successful_runs", "failed_runs", "downloads", "sessions")


def project(lon: float, lat: float) -> tuple[float, float]:
    x = (lon + 180.0) / 360.0 * WIDTH
    lat = min(max(lat, LAT_MIN), LAT_MAX)
    y = (lat - LAT_MIN) / (LAT_MAX - LAT_MIN) * HEIGHT
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


def rings_for(geometry) -> list[list]:
    if geometry["type"] == "Polygon":
        return geometry["coordinates"]
    if geometry["type"] == "MultiPolygon":
        return [ring for polygon in geometry["coordinates"] for ring in polygon]
    return []


def _country_polygons(geojson):
    """Yield (iso3, [projected+simplified rings]) for every non-Antarctic country."""
    for feature in geojson["features"]:
        iso3 = feature.get("properties", {}).get(ISO_KEY)
        if not iso3 or iso3 == "ATA":
            continue
        rings = []
        for ring in rings_for(feature["geometry"]):
            if not ring:
                continue
            if max(pt[1] for pt in ring) < LAT_MIN:
                continue
            pts = simplify([project(lon, lat) for lon, lat in ring], TOLERANCE)
            if len(pts) < 3:
                continue
            rings.append(pts)
        if rings:
            yield iso3, rings


def render_usage_map(rows: list[dict[str, object]], metric: str = "successful_runs") -> bytes:
    if metric not in MAP_METRICS:
        metric = "successful_runs"
    values = {
        row["country_iso3"]: float(row.get(metric) or 0)
        for row in rows if row.get("country_iso3")
    }
    max_value = max(values.values()) if values else 0
    geojson = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))

    fig, ax = plt.subplots(figsize=(10, 5), dpi=100)
    ax.set_facecolor("#f5f7f5")
    for iso3, rings in _country_polygons(geojson):
        value = values.get(iso3, 0)
        color = TEAL_CMAP((value / max_value) ** 0.7) if value > 0 and max_value else "#eef2f0"
        for pts in rings:
            ax.add_patch(Polygon(pts, closed=True, facecolor=color, edgecolor="#ffffff", linewidth=0.6))
    ax.set_xlim(0, WIDTH)
    ax.set_ylim(0, HEIGHT)
    ax.set_aspect("equal")
    ax.axis("off")

    sm = ScalarMappable(norm=Normalize(0, max_value or 1), cmap=TEAL_CMAP)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, pad=0.01, fraction=0.03)
    cbar.set_label("Runs", fontsize=12)
    cbar.ax.tick_params(labelsize=11)
    cbar.outline.set_linewidth(0.5)

    fig.tight_layout(pad=0.2)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="#f5f7f5", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
