#!/usr/bin/env python3
"""Run one cropped LISFLOOD-FP surface-flood scenario."""

from __future__ import annotations

import json
import gzip
import hashlib
import math
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

RETURN_PERIODS = (5, 10, 20, 50, 100)
HAZARD_SUFFIX = ".maxHaz"
PARAMETER_VERSION = "surface-v1"
RISK_MATRIX = np.array(
    [[1, 1, 1, 2], [1, 2, 2, 3], [2, 2, 3, 4], [2, 3, 4, 4]],
    dtype=np.uint8,
)
NODATA = -9999.0
PARAMETERS = """\
DEMfile dem.asc
resroot result
dirroot results
sim_time 43200
initial_tstep 10
massint 3600
saveint 3600
acceleration
fpfric 0.06
infiltration 0.00001
hazard
depththresh 0.01
comp_out
rainfall design.rain
evaporation evaporation.evap
"""
EVAPORATION = (
    "QTBDY   Obtained from results file C:\\HALCROW\\KISMOD\\KISL_100.ZZN\r\n"
    "3\thours\r\n"
    "12            3\r\n"
    "19.2\t20\r\n"
    "30\t24"
)


def design_storm(period: int, minutes: int = 180, peak_ratio: float = 0.393) -> np.ndarray:
    """Return a one-minute Chicago storm in mm/hour for Nanjing Jiangnan."""
    if period not in RETURN_PERIODS:
        raise ValueError(f"unsupported return period: {period}")
    amplitude, offset, decay = 16.696 * (1 + 0.954 * math.log10(period)), 18.825, 0.751
    peak = minutes * peak_ratio
    midpoint = np.arange(minutes, dtype=float) + 0.5
    scaled = np.where(midpoint <= peak, (peak - midpoint) / peak_ratio, (midpoint - peak) / (1 - peak_ratio))
    rates = amplitude * (offset + (1 - decay) * scaled) / (offset + scaled) ** (decay + 1)
    expected_depth = amplitude / (minutes + offset) ** decay * minutes
    rates *= expected_depth / (rates.sum() / 60)
    return rates


def transform_points(points, source: str, target: str) -> list[tuple[float, float]]:
    result = subprocess.run(
        ["gdaltransform", "-s_srs", source, "-t_srs", target],
        input="".join(f"{x} {y}\n" for x, y in points),
        text=True,
        capture_output=True,
        check=True,
    )
    return [tuple(map(float, line.split()[:2])) for line in result.stdout.splitlines()]


def snap_bounds(
    bounds: list[list[float]], header: dict[str, float], max_area_km2: float
) -> tuple[tuple[int, int, int, int], list[list[float]]]:
    (south, west), (north, east) = bounds
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("invalid bounds")
    (x0, y0), (x1, y1) = transform_points(
        [(west, south), (east, north)], "EPSG:4326", "EPSG:32650"
    )
    origin_x, origin_y, cell = (
        header["xllcorner"],
        header["yllcorner"],
        header["cellsize"],
    )
    window = (
        max(0, math.ceil((x0 - origin_x) / cell)),
        max(0, math.ceil((y0 - origin_y) / cell)),
        min(int(header["ncols"]), math.floor((x1 - origin_x) / cell)),
        min(int(header["nrows"]), math.floor((y1 - origin_y) / cell)),
    )
    c0, r0, c1, r1 = window
    if (
        c0 >= c1
        or r0 >= r1
        or x0 < origin_x
        or y0 < origin_y
        or x1 > origin_x + header["ncols"] * cell
        or y1 > origin_y + header["nrows"] * cell
    ):
        raise ValueError("bounds outside available extent")
    area = (c1 - c0) * (r1 - r0) * cell * cell / 1e6
    if area > max_area_km2:
        raise ValueError(f"area exceeds {max_area_km2:g} km²")
    corners = transform_points(
        [
            (origin_x + c0 * cell, origin_y + r0 * cell),
            (origin_x + c1 * cell, origin_y + r1 * cell),
        ],
        "EPSG:32650",
        "EPSG:4326",
    )
    return window, [[corners[0][1], corners[0][0]], [corners[1][1], corners[1][0]]]


def crop_grid(data: np.ndarray, window: tuple[int, int, int, int]) -> np.ndarray:
    c0, r0, c1, r1 = window
    return data[data.shape[0] - r1 : data.shape[0] - r0, c0:c1]


def write_ascii(path: Path, header: dict[str, float], data: np.ndarray) -> None:
    lines = (
        f"ncols {data.shape[1]}\n",
        f"nrows {data.shape[0]}\n",
        f"xllcorner {header['xllcorner']:.6f}\n",
        f"yllcorner {header['yllcorner']:.6f}\n",
        f"cellsize {header['cellsize']:.6f}\n",
        f"NODATA_value {NODATA:g}\n",
    )
    with path.open("w", encoding="utf-8") as target:
        target.writelines(lines)
        np.savetxt(
            target,
            np.where(np.isfinite(data), data, NODATA),
            fmt="%.6f",
        )


def job_id(window, period, model, data_version) -> str:
    value = f"{','.join(map(str, window))}|{period}|{model}|{PARAMETER_VERSION}|{data_version}"
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def build_risk(depth: np.ndarray, hazard: np.ndarray, population: np.ndarray) -> tuple[np.ndarray, list[float]]:
    if depth.shape != hazard.shape or depth.shape != population.shape:
        raise ValueError("depth, hazard, and population grids must align")
    populated = population[np.isfinite(population) & (population > 0)]
    if populated.size == 0:
        breaks = [0.0, 0.0, 0.0]
        exposure_class = np.zeros(population.shape, dtype=np.intp)
    else:
        breaks = np.quantile(populated, [0.25, 0.5, 0.75]).tolist()
        exposure_class = np.digitize(population, breaks).clip(0, 3)
    hazard_class = np.digitize(hazard, [0.75, 1.25, 2.5]).clip(0, 3)
    risk = RISK_MATRIX[hazard_class, exposure_class]
    risk[(depth < 0.10) | ~np.isfinite(depth) | ~np.isfinite(hazard)] = 0
    return risk, breaks


def read_ascii(path: Path) -> tuple[dict[str, float], np.ndarray]:
    header: dict[str, float] = {}
    opener = gzip.open if path.suffix == ".gz" else Path.open
    with opener(path, "rt", encoding="utf-8") as source:
        for _ in range(6):
            key, value = source.readline().split()[:2]
            header[key.lower()] = float(value)
        data = np.loadtxt(source)
    expected = (int(header["nrows"]), int(header["ncols"]))
    if data.shape != expected:
        raise ValueError(f"{path} has shape {data.shape}, expected {expected}")
    data[data == header.get("nodata_value", NODATA)] = np.nan
    return header, data


def assert_aligned(reference: dict[str, float], candidate: dict[str, float]) -> None:
    for key in ("ncols", "nrows", "xllcorner", "yllcorner", "cellsize"):
        if not math.isclose(reference[key], candidate[key], rel_tol=0, abs_tol=1e-6):
            raise ValueError(f"grid mismatch for {key}: {reference[key]} != {candidate[key]}")


def write_rainfall(path: Path, rates: np.ndarray) -> None:
    series = np.append(rates, 0.0)
    integrated = np.sum((series[:-1] + series[1:]) * 0.5) / 60
    series *= (rates.sum() / 60) / integrated
    rows = [f"{len(series)}\tseconds"]
    rows.extend(f"{rate:.8f}\t{minute * 60}" for minute, rate in enumerate(series))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def locate_output(root: Path, suffix: str) -> Path:
    matches = sorted(path for path in root.rglob(f"*{suffix}") if path.is_file())
    if len(matches) != 1:
        raise RuntimeError(f"expected one {suffix} output, found {len(matches)}")
    return matches[0]


def mass_balance_error(path: Path) -> float:
    """Return cumulative volume error relative to the simulated water volume."""
    lines = [line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    try:
        header = next(row for row in lines if "Verror" in row)
        volume_index = header.index("Vol")
        error_index = header.index("Verror")
        rain_index = header.index("Rain-Inf+Evap")
    except (StopIteration, ValueError) as error:
        raise ValueError(f"unrecognised mass-balance file: {path}") from error
    rows = [row for row in lines if len(row) > rain_index and row[0][0].isdigit()]
    if not rows:
        raise ValueError(f"mass-balance file has no data: {path}")
    cumulative_error = sum(float(row[error_index]) for row in rows)
    scale = max(
        max(abs(float(row[volume_index])) for row in rows),
        max(abs(float(row[rain_index])) for row in rows),
        1.0,
    )
    return abs(cumulative_error) / scale


def save_layer(path: Path, data: np.ndarray, palette: list[tuple[int, int, int, int]], classes: bool = False) -> None:
    rgba = np.zeros((*data.shape, 4), dtype=np.uint8)
    valid = np.isfinite(data)
    if classes:
        for value, colour in enumerate(palette):
            rgba[(data == value) & valid] = colour
    elif np.any(valid):
        low, high = np.nanpercentile(data, [2, 98])
        scale = np.clip((data - low) / max(high - low, 1e-9), 0, 1)
        indexes = np.minimum((np.nan_to_num(scale) * (len(palette) - 1)).astype(np.uint8), len(palette) - 1)
        for value, colour in enumerate(palette):
            rgba[(indexes == value) & valid] = colour
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)


def model_version(engine: Path) -> str:
    for flag in ("-version", "-v"):
        result = subprocess.run([str(engine), flag], text=True, capture_output=True)
        output = (result.stdout + result.stderr).strip()
        if output:
            match = re.search(r"LISFLOOD-FP version\s+([\d.]+)", output, re.IGNORECASE)
            version = match.group(1) if match else output.splitlines()[0][:120]
            return f"{version} ACC"
    return "unknown ACC"


def run_job(
    engine: Path,
    base_header: dict[str, float],
    dem: np.ndarray,
    population: np.ndarray,
    window: tuple[int, int, int, int],
    period: int,
    effective_bounds: list[list[float]],
    data_version: str,
    staging: Path,
    timeout: int = 7200,
) -> dict:
    """Run one cropped ACC rainfall scenario and write a flat manifest to staging."""
    c0, r0, c1, r1 = window
    cell = base_header["cellsize"]
    header = dict(
        base_header,
        ncols=float(c1 - c0),
        nrows=float(r1 - r0),
        xllcorner=base_header["xllcorner"] + c0 * cell,
        yllcorner=base_header["yllcorner"] + r0 * cell,
    )
    cropped_dem = crop_grid(dem, window)
    cropped_population = np.nan_to_num(crop_grid(population, window), nan=0.0)
    staging.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="lisflood-job-") as directory:
        work = Path(directory)
        write_ascii(work / "dem.asc", header, cropped_dem)
        rates = design_storm(period)
        write_rainfall(work / "design.rain", rates)
        (work / "evaporation.evap").write_text(EVAPORATION, encoding="utf-8")
        (work / "web.par").write_text(PARAMETERS, encoding="utf-8")
        subprocess.run([str(engine), "web.par"], cwd=work, check=True, timeout=timeout)
        output = work / "results"
        error = mass_balance_error(locate_output(output, ".mass"))
        if error > 0.03:
            raise RuntimeError(f"mass-balance error is {error:.2%}")
        depth_header, depth = read_ascii(locate_output(output, ".max"))
        hazard_header, hazard = read_ascii(locate_output(output, HAZARD_SUFFIX))
        assert_aligned(header, depth_header)
        assert_aligned(header, hazard_header)
        if np.nanmin(depth) < -1e-8:
            raise RuntimeError("negative depth")

    risk, breaks = build_risk(depth, hazard, cropped_population)
    flooded = np.isfinite(depth) & (depth >= 0.10)
    save_layer(
        staging / "dem.png",
        cropped_dem,
        [(20 + i * 24, 55 + i * 18, 45 + i * 15, 190) for i in range(10)],
    )
    save_layer(
        staging / "population.png",
        cropped_population,
        [(83 + i * 12, 35, 130 + i * 10, 30 + i * 20) for i in range(10)],
    )
    save_layer(
        staging / "depth.png",
        np.where(flooded, depth, np.nan),
        [(16, 100 + i * 14, 180 + i * 7, 30 + i * 20) for i in range(10)],
    )
    save_layer(
        staging / "hazard.png",
        np.where(flooded, np.digitize(hazard, [0.75, 1.25, 2.5]) + 1, 0),
        [
            (0, 0, 0, 0),
            (254, 229, 153, 170),
            (253, 174, 97, 190),
            (240, 59, 32, 210),
            (122, 1, 119, 220),
        ],
        True,
    )
    save_layer(
        staging / "risk.png",
        risk,
        [
            (0, 0, 0, 0),
            (49, 163, 84, 175),
            (254, 224, 139, 190),
            (244, 109, 67, 210),
            (165, 0, 38, 225),
        ],
        True,
    )
    manifest = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "modelVersion": model_version(engine),
        "dataVersion": data_version,
        "returnPeriod": period,
        "rainfallMm": round(float(rates.sum() / 60), 3),
        "bounds": effective_bounds,
        "populationBreaks": [round(float(value), 6) for value in breaks],
        "layers": {name: f"{name}.png" for name in ("dem", "population", "depth", "hazard", "risk")},
        "stats": {
            "floodedAreaKm2": round(float(flooded.sum() * cell * cell / 1e6), 3),
            "exposedPopulation": round(float(cropped_population[flooded].sum())),
            "maximumDepthM": round(float(np.nanmax(depth)), 3),
        },
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
