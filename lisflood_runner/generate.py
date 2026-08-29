#!/usr/bin/env python3
"""Run one cropped LISFLOOD-FP surface-flood scenario."""

from __future__ import annotations

import json
import gzip
import hashlib
import math
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

RETURN_PERIODS = (5, 10, 20, 50, 100)
HAZARD_SUFFIX = ".maxHaz"
PARAMETER_VERSION = "surface-v3"
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
depththresh 0.001
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
        timeout=10,
    )
    return [tuple(map(float, line.split()[:2])) for line in result.stdout.splitlines()]


def snap_bounds(
    bounds: list[list[float]], header: dict[str, float], max_area_km2: float
) -> tuple[tuple[int, int, int, int], list[list[float]]]:
    normalised_bounds = _normalise_bounds(bounds)
    max_area = _normalise_max_area(max_area_km2)
    grid_header, ncols, nrows, cell = _normalise_grid_header(header)
    (south, west), (north, east) = normalised_bounds
    projected = transform_points(
        [
            (west, south),
            (west, north),
            (east, south),
            (east, north),
        ],
        "EPSG:4326",
        "EPSG:32650",
    )
    if len(projected) != 4:
        raise ValueError("coordinate transform returned the wrong number of points")
    if any(
        len(point) < 2
        or not math.isfinite(float(point[0]))
        or not math.isfinite(float(point[1]))
        for point in projected
    ):
        raise ValueError("coordinate transform returned invalid points")
    x0 = min(point[0] for point in projected)
    y0 = min(point[1] for point in projected)
    x1 = max(point[0] for point in projected)
    y1 = max(point[1] for point in projected)
    origin_x, origin_y = grid_header["xllcorner"], grid_header["yllcorner"]
    window = (
        max(0, math.ceil((x0 - origin_x) / cell)),
        max(0, math.ceil((y0 - origin_y) / cell)),
        min(ncols, math.floor((x1 - origin_x) / cell)),
        min(nrows, math.floor((y1 - origin_y) / cell)),
    )
    c0, r0, c1, r1 = window
    if (
        c0 >= c1
        or r0 >= r1
        or x0 < origin_x
        or y0 < origin_y
        or x1 > origin_x + ncols * cell
        or y1 > origin_y + nrows * cell
    ):
        raise ValueError("bounds outside available extent")
    area = (c1 - c0) * (r1 - r0) * cell * cell / 1e6
    if area > max_area:
        raise ValueError(f"area exceeds {max_area:g} km²")
    corners = transform_points(
        [
            (origin_x + c0 * cell, origin_y + r0 * cell),
            (origin_x + c0 * cell, origin_y + r1 * cell),
            (origin_x + c1 * cell, origin_y + r0 * cell),
            (origin_x + c1 * cell, origin_y + r1 * cell),
        ],
        "EPSG:32650",
        "EPSG:4326",
    )
    if len(corners) != 4:
        raise ValueError("coordinate transform returned the wrong number of points")
    if any(
        len(point) < 2
        or not math.isfinite(float(point[0]))
        or not math.isfinite(float(point[1]))
        for point in corners
    ):
        raise ValueError("coordinate transform returned invalid points")
    longitudes = [point[0] for point in corners]
    latitudes = [point[1] for point in corners]
    return window, [
        [float(min(latitudes)), float(min(longitudes))],
        [float(max(latitudes)), float(max(longitudes))],
    ]


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


def _normalise_integer(value, label: str) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{label} must contain integer values")
    return int(value)


def _normalise_float(value, label: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value, (int, float, np.integer, np.floating)
    ):
        raise ValueError(f"{label} must be numeric")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{label} must be finite") from error
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _normalise_grid_header(
    base_header: dict[str, float],
) -> tuple[dict[str, float], int, int, float]:
    try:
        ncols_float = _normalise_float(base_header["ncols"], "base header ncols")
        nrows_float = _normalise_float(base_header["nrows"], "base header nrows")
        origin_x = _normalise_float(base_header["xllcorner"], "base header xllcorner")
        origin_y = _normalise_float(base_header["yllcorner"], "base header yllcorner")
        cell = _normalise_float(base_header["cellsize"], "base header cellsize")
    except (KeyError, TypeError) as error:
        raise ValueError("base header is incomplete or non-numeric") from error
    if (
        not ncols_float.is_integer()
        or not nrows_float.is_integer()
        or ncols_float <= 0
        or nrows_float <= 0
        or cell <= 0
    ):
        raise ValueError("base header has invalid dimensions or cellsize")
    ncols, nrows = int(ncols_float), int(nrows_float)
    header = dict(base_header)
    header.update(
        ncols=float(ncols),
        nrows=float(nrows),
        xllcorner=origin_x,
        yllcorner=origin_y,
        cellsize=cell,
    )
    return header, ncols, nrows, cell


def _normalise_max_area(max_area_km2) -> float:
    max_area = _normalise_float(max_area_km2, "max_area_km2")
    if max_area <= 0:
        raise ValueError("max_area_km2 must be positive")
    return max_area


def _normalise_window(window, ncols: int, nrows: int) -> tuple[int, int, int, int]:
    try:
        values = tuple(window)
    except TypeError as error:
        raise ValueError("window must contain four integer values") from error
    if len(values) != 4:
        raise ValueError("window must contain four integer values")
    c0, r0, c1, r1 = (_normalise_integer(value, "window") for value in values)
    if not (0 <= c0 < c1 <= ncols and 0 <= r0 < r1 <= nrows):
        raise ValueError("window is outside the base grid")
    return c0, r0, c1, r1


def _normalise_period(period: int) -> int:
    if isinstance(period, (bool, np.bool_)) or not isinstance(period, (int, np.integer)):
        raise ValueError("return period must be an integer")
    period = int(period)
    if period not in RETURN_PERIODS:
        raise ValueError(f"unsupported return period: {period}")
    return period


def _normalise_bounds(bounds) -> list[list[float]]:
    try:
        corners = tuple(bounds)
    except (TypeError, ValueError) as error:
        raise ValueError("effective bounds must contain two corners") from error
    if len(corners) != 2:
        raise ValueError("effective bounds must contain two corners")
    normalised: list[list[float]] = []
    for corner in corners:
        try:
            values = tuple(corner)
        except (TypeError, ValueError) as error:
            raise ValueError("effective bounds must contain two coordinates per corner") from error
        if len(values) != 2:
            raise ValueError("effective bounds must contain two coordinates per corner")
        coordinates = [_normalise_float(value, "effective bounds coordinate") for value in values]
        normalised.append(coordinates)
    (south, west), (north, east) = normalised
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("effective bounds must be ordered WGS84 coordinates")
    return normalised


def _normalise_header(
    base_header: dict[str, float], dem: np.ndarray, population: np.ndarray
) -> tuple[dict[str, float], np.ndarray, np.ndarray, int, int, float]:
    header, ncols, nrows, cell = _normalise_grid_header(base_header)
    dem_array, population_array = np.asarray(dem), np.asarray(population)
    expected = (nrows, ncols)
    if dem_array.ndim != 2 or dem_array.shape != expected:
        raise ValueError(f"DEM grid must be 2-D with shape {expected}")
    if population_array.ndim != 2 or population_array.shape != expected:
        raise ValueError(f"population grid must be 2-D with shape {expected}")
    return header, dem_array, population_array, ncols, nrows, cell


def _resolve_engine(engine: Path) -> Path:
    path = Path(engine).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"LISFLOOD engine not found: {path}")
    if not os.access(path, os.X_OK):
        raise PermissionError(f"LISFLOOD engine is not executable: {path}")
    return path


def _validate_job_inputs(
    engine: Path,
    base_header: dict[str, float],
    dem: np.ndarray,
    population: np.ndarray,
    window,
    period: int,
    effective_bounds,
    data_version: str,
) -> tuple[Path, dict[str, float], np.ndarray, np.ndarray, tuple[int, int, int, int], int, list[list[float]], str, float]:
    header, dem_array, population_array, ncols, nrows, cell = _normalise_header(
        base_header, dem, population
    )
    normalised_window = _normalise_window(window, ncols, nrows)
    normalised_period = _normalise_period(period)
    normalised_bounds = _normalise_bounds(effective_bounds)
    resolved_engine = _resolve_engine(engine)
    return (
        resolved_engine,
        header,
        dem_array,
        population_array,
        normalised_window,
        normalised_period,
        normalised_bounds,
        str(data_version),
        cell,
    )


def build_risk(depth: np.ndarray, hazard: np.ndarray, population: np.ndarray) -> tuple[np.ndarray, list[float]]:
    if depth.shape != hazard.shape or depth.shape != population.shape:
        raise ValueError("depth, hazard, and population grids must align")
    safe_population = np.where(np.isfinite(population), population, 0.0)
    populated = safe_population[safe_population > 0]
    if populated.size == 0:
        breaks = [0.0, 0.0, 0.0]
        exposure_class = np.zeros(safe_population.shape, dtype=np.intp)
    else:
        breaks = np.quantile(populated, [0.25, 0.5, 0.75]).tolist()
        exposure_class = np.digitize(safe_population, breaks).clip(0, 3)
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
        data = np.loadtxt(source, ndmin=2)
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
    rows = ["# LISFLOOD-FP rainfall time series", f"{len(series)}\tseconds"]
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
        rain_index = header.index("Rain-(Inf+Evap)")
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
    engine = _resolve_engine(engine)
    for flag in ("-version", "-v"):
        try:
            result = subprocess.run(
                [str(engine), flag],
                text=True,
                capture_output=True,
                check=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
        output = (result.stdout + result.stderr).strip()
        for line in output.splitlines():
            if (
                re.search(r"\bLISFLOOD[- ]FP\b", line, re.IGNORECASE)
                and re.search(r"\b8\.0\.3\b", line)
                and not re.search(r"\b(?:usage|error|invalid)\b", line, re.IGNORECASE)
            ):
                return "8.0.3 ACC"
    raise RuntimeError("LISFLOOD-FP 8.0.3 version output was not found")


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
    (
        engine,
        base_header,
        dem,
        population,
        window,
        period,
        effective_bounds,
        data_version,
        cell,
    ) = _validate_job_inputs(
        engine,
        base_header,
        dem,
        population,
        window,
        period,
        effective_bounds,
        data_version,
    )
    c0, r0, c1, r1 = window
    header = dict(
        base_header,
        ncols=float(c1 - c0),
        nrows=float(r1 - r0),
        xllcorner=base_header["xllcorner"] + c0 * cell,
        yllcorner=base_header["yllcorner"] + r0 * cell,
    )
    cropped_dem = crop_grid(dem, window)
    if not np.isfinite(cropped_dem).any():
        raise ValueError("cropped DEM has no finite cells")
    cropped_population = np.nan_to_num(
        crop_grid(population, window), nan=0.0, posinf=0.0, neginf=0.0
    )
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
        if not np.isfinite(depth).any():
            raise ValueError("depth output has no finite depth values")
        if np.isinf(depth).any():
            raise ValueError("depth output contains non-finite depth values")
        maximum_depth = float(np.nanmax(depth))
        if not math.isfinite(maximum_depth):
            raise ValueError("maximum depth is not finite")
        if np.min(depth[np.isfinite(depth)]) < -1e-8:
            raise RuntimeError("negative depth")

    risk, breaks = build_risk(depth, hazard, cropped_population)
    flooded = np.isfinite(depth) & np.isfinite(hazard) & (depth >= 0.10)
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
            "maximumDepthM": round(maximum_depth, 3),
        },
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return manifest
