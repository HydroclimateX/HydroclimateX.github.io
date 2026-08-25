#!/usr/bin/env python3
"""Compile the private engine, run five storms, and atomically publish map assets."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

RETURN_PERIODS = (5, 10, 20, 50, 100)
HAZARD_SUFFIX = ".maxHaz"
RISK_MATRIX = np.array(
    [[1, 1, 1, 2], [1, 2, 2, 3], [2, 2, 3, 4], [2, 3, 4, 4]],
    dtype=np.uint8,
)
NODATA = -9999.0


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


def build_risk(depth: np.ndarray, hazard: np.ndarray, population: np.ndarray) -> tuple[np.ndarray, list[float]]:
    if depth.shape != hazard.shape or depth.shape != population.shape:
        raise ValueError("depth, hazard, and population grids must align")
    populated = population[np.isfinite(population) & (population > 0)]
    if populated.size == 0:
        raise ValueError("population grid has no positive cells")
    breaks = np.quantile(populated, [0.25, 0.5, 0.75]).tolist()
    hazard_class = np.digitize(hazard, [0.75, 1.25, 2.5]).clip(0, 3)
    exposure_class = np.digitize(population, breaks).clip(0, 3)
    risk = RISK_MATRIX[hazard_class, exposure_class]
    risk[(depth < 0.10) | ~np.isfinite(depth) | ~np.isfinite(hazard)] = 0
    return risk, breaks


def publish_cache(root: Path, staging: Path, run_id: str, manifest: dict) -> None:
    if set(manifest.get("scenarios", {})) != {str(p) for p in RETURN_PERIODS}:
        raise ValueError("all five scenarios are required before publishing")
    final = root / run_id
    staging.replace(final)
    temporary = root / ".manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, root / "manifest.json")
    runs = sorted((path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")), reverse=True)
    for old in runs[2:]:
        shutil.rmtree(old)


def ensure_cache_space(cache: Path, minimum_gb: int = 15) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(cache).free < minimum_gb * 1024**3:
        raise RuntimeError(f"at least {minimum_gb} GB free disk space is required")


def read_ascii(path: Path) -> tuple[dict[str, float], np.ndarray]:
    header: dict[str, float] = {}
    with path.open(encoding="utf-8") as source:
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
    series *= (rates.sum() / 60) / np.trapz(series, dx=1 / 60)
    rows = [f"{len(series)}\tseconds"]
    rows.extend(f"{rate:.8f}\t{minute * 60}" for minute, rate in enumerate(series))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def set_parameter(text: str, key: str, value: str) -> str:
    line = f"{key:<18} {value}"
    pattern = re.compile(rf"^(?!\s*#)\s*{re.escape(key)}\b.*$", re.IGNORECASE | re.MULTILINE)
    return pattern.sub(line, text, count=1) if pattern.search(text) else text.rstrip() + "\n" + line + "\n"


def compile_engine(source: Path, build: Path) -> Path:
    searchable = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in source.rglob("*")
        if path.suffix.lower() in {".c", ".cc", ".cpp", ".h", ".hpp"}
    )
    for token in ("uniform_rules", "inpFile"):
        if token not in searchable:
            raise RuntimeError(f"private source lacks required SWMM feature: {token}")
    subprocess.run(["cmake", "-S", str(source), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release"], check=True)
    subprocess.run(["cmake", "--build", str(build), "--parallel", "2"], check=True)
    candidates = [path for path in build.rglob("lisflood") if path.is_file() and os.access(path, os.X_OK)]
    if not candidates:
        raise RuntimeError("CMake did not produce a lisflood executable")
    return candidates[0]


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


def wgs84_bounds(header: dict[str, float]) -> list[list[float]]:
    x0, y0 = header["xllcorner"], header["yllcorner"]
    x1 = x0 + header["ncols"] * header["cellsize"]
    y1 = y0 + header["nrows"] * header["cellsize"]
    command = ["gdaltransform", "-s_srs", "EPSG:32650", "-t_srs", "EPSG:4326"]
    result = subprocess.run(command, input=f"{x0} {y0}\n{x1} {y1}\n", text=True, capture_output=True, check=True)
    points = [[float(value) for value in line.split()[:2]] for line in result.stdout.splitlines()]
    return [[points[0][1], points[0][0]], [points[1][1], points[1][0]]]


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
            return match.group(1) if match else output.splitlines()[0][:120]
    return "unknown"


def parity_check(reference: Path, generated: np.ndarray, header: dict[str, float]) -> None:
    ref_header, expected = read_ascii(reference)
    assert_aligned(header, ref_header)
    mask = np.isfinite(generated) & np.isfinite(expected)
    if not np.any(mask):
        raise RuntimeError("Windows reference has no comparable cells")
    mae = float(np.mean(np.abs(generated[mask] - expected[mask])))
    wet_generated, wet_expected = generated[mask] >= 0.10, expected[mask] >= 0.10
    area_difference = abs(wet_generated.sum() - wet_expected.sum()) / max(int(wet_expected.sum()), 1)
    if mae > 0.001 or area_difference > 0.01:
        raise RuntimeError(f"Windows parity failed: MAE={mae:.6f}m, wet-area difference={area_difference:.2%}")


def run_all(private: Path, cache: Path) -> None:
    source, model = private / "source", private / "model"
    parameter_name = os.getenv("LISFLOOD_PARAMETER_FILE", "ft.par")
    for required in (source / "CMakeLists.txt", model / parameter_name, model / "dem.asc", model / "population.asc"):
        if not required.is_file():
            raise FileNotFoundError(required)
    ensure_cache_space(cache)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    staging = cache / f".{run_id}.tmp"
    staging.mkdir()
    try:
        with tempfile.TemporaryDirectory(prefix="lisflood-") as temporary:
            temporary_root = Path(temporary)
            engine = compile_engine(source, temporary_root / "build")
            dem_header, dem = read_ascii(model / "dem.asc")
            population_header, population = read_ascii(model / "population.asc")
            assert_aligned(dem_header, population_header)
            population = np.nan_to_num(population, nan=0.0)
            bounds = wgs84_bounds(dem_header)

            save_layer(staging / "dem.png", dem, [(20 + i * 24, 55 + i * 18, 45 + i * 15, 190) for i in range(10)])
            save_layer(staging / "population.png", population, [(83 + i * 12, 35, 130 + i * 10, 30 + i * 20) for i in range(10)])

            manifest = {
                "schemaVersion": 1,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "modelVersion": model_version(engine),
                "bounds": bounds,
                "populationBreaks": [],
                "baseLayers": {
                    "dem": f"/results/{run_id}/dem.png",
                    "population": f"/results/{run_id}/population.png",
                },
                "scenarios": {},
            }
            parameter_template = (model / parameter_name).read_text(encoding="utf-8")
            for period in RETURN_PERIODS:
                scenario = temporary_root / str(period)
                shutil.copytree(model, scenario, ignore=shutil.ignore_patterns("windows-reference"))
                rates = design_storm(period)
                write_rainfall(scenario / "design.rain", rates)
                output = scenario / "results"
                parameters = set_parameter(parameter_template, "rainfall", "design.rain")
                parameters = set_parameter(parameters, "hazard", "")
                parameters = set_parameter(parameters, "sim_time", "43200")
                parameters = set_parameter(parameters, "resroot", f"return-{period}")
                parameters = set_parameter(parameters, "dirroot", "results")
                (scenario / "web.par").write_text(parameters, encoding="utf-8")
                subprocess.run([str(engine), "web.par"], cwd=scenario, check=True)
                error = mass_balance_error(locate_output(output, ".mass"))
                if error > 0.03:
                    raise RuntimeError(f"mass-balance error is {error:.2%} for the {period}-year scenario")

                depth_header, depth = read_ascii(locate_output(output, ".max"))
                hazard_header, hazard = read_ascii(locate_output(output, HAZARD_SUFFIX))
                assert_aligned(dem_header, depth_header)
                assert_aligned(dem_header, hazard_header)
                if np.nanmin(depth) < -1e-8:
                    raise RuntimeError(f"negative depth in {period}-year result")
                reference = model / "windows-reference" / f"{period}.max"
                if os.getenv("LISFLOOD_REQUIRE_PARITY", "1") != "0":
                    if not reference.is_file():
                        raise FileNotFoundError(reference)
                    parity_check(reference, depth, depth_header)

                risk, breaks = build_risk(depth, hazard, population)
                flooded = np.isfinite(depth) & (depth >= 0.10)
                manifest["populationBreaks"] = [round(value, 6) for value in breaks]
                folder = staging / str(period)
                folder.mkdir()
                save_layer(folder / "depth.png", np.where(flooded, depth, np.nan),
                           [(16, 100 + i * 14, 180 + i * 7, 30 + i * 20) for i in range(10)])
                save_layer(folder / "hazard.png", np.where(flooded, np.digitize(hazard, [0.75, 1.25, 2.5]) + 1, 0),
                           [(0, 0, 0, 0), (254, 229, 153, 170), (253, 174, 97, 190), (240, 59, 32, 210), (122, 1, 119, 220)], True)
                save_layer(folder / "risk.png", risk,
                           [(0, 0, 0, 0), (49, 163, 84, 175), (254, 224, 139, 190), (244, 109, 67, 210), (165, 0, 38, 225)], True)
                manifest["scenarios"][str(period)] = {
                    "rainfallMm": round(float(rates.sum() / 60), 3),
                    "layers": {name: f"/results/{run_id}/{period}/{name}.png" for name in ("depth", "hazard", "risk")},
                    "stats": {
                        "floodedAreaKm2": round(float(flooded.sum() * dem_header["cellsize"] ** 2 / 1e6), 3),
                        "exposedPopulation": round(float(population[flooded].sum())),
                        "maximumDepthM": round(float(np.nanmax(depth)), 3),
                    },
                }
                shutil.rmtree(scenario)
        publish_cache(cache, staging, run_id, manifest)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


if __name__ == "__main__":
    run_all(
        Path(os.getenv("LISFLOOD_PRIVATE_DIR", "/opt/lisflood/private")),
        Path(os.getenv("LISFLOOD_CACHE_DIR", "/opt/lisflood/cache")),
    )
