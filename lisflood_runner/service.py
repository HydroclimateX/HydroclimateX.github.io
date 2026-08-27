"""Small stdlib HTTP service for queued LISFLOOD simulations."""

from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import re
import shutil
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from numbers import Integral
from pathlib import Path
from urllib.parse import urlsplit

from . import generate


DEFAULT_WINDOW = (460, 124, 1037, 701)
RETURN_PERIODS = (5, 10, 20, 50, 100)
MAX_BODY_BYTES = 4096
JOB_ID_PATTERN = re.compile(r"[0-9a-f]{20}\Z")
STALE_TEMP_PATTERN = re.compile(r"\.[0-9a-f]{20}\.tmp\Z")
EXPECTED_LAYER_NAMES = frozenset({"dem", "population", "depth", "hazard", "risk"})
FAILED_STATE_LIMIT = 64


class QueueFull(Exception):
    """The bounded in-memory job queue has no pending capacity."""


class InsufficientStorage(Exception):
    """The cache filesystem has less free space than configured."""


class EngineUnavailable(Exception):
    """The configured LISFLOOD executable cannot be used."""


def ensure_cache_space(cache_dir: Path, minimum_free_gb: float) -> None:
    """Raise :class:`InsufficientStorage` when the cache is too full."""
    try:
        minimum = float(minimum_free_gb)
    except (TypeError, ValueError, OverflowError) as error:
        raise InsufficientStorage("Insufficient storage") from error
    if not math.isfinite(minimum) or minimum < 0:
        raise InsufficientStorage("Insufficient storage")
    try:
        free_bytes = shutil.disk_usage(cache_dir).free
    except OSError as error:
        raise InsufficientStorage("Insufficient storage") from error
    if free_bytes < minimum * 1024**3:
        raise InsufficientStorage("Insufficient storage")


def _normalise_period(period) -> int:
    if isinstance(period, bool) or not isinstance(period, Integral):
        raise ValueError("return period must be an integer")
    period = int(period)
    if period not in RETURN_PERIODS:
        raise ValueError(f"unsupported return period: {period}")
    return period


def _reject_json_constant(value: str):
    raise ValueError(f"invalid JSON number: {value}")


def _safe_filename(value: str) -> bool:
    return bool(
        isinstance(value, str)
        and value
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
        and not Path(value).is_absolute()
        and Path(value).name == value
    )


class Service:
    """Own a bounded FIFO queue and one optional background worker."""

    def __init__(
        self,
        cache_dir,
        engine,
        header,
        dem,
        population,
        data_version,
        model_version,
        max_area=300,
        timeout=7200,
        minimum_free_gb=15,
        runner=generate.run_job,
        start_worker=True,
    ) -> None:
        self.cache_dir = Path(cache_dir).expanduser()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.engine = Path(engine).expanduser()
        self.header = dict(header)
        self.dem = dem
        self.population = population
        self.data_version = str(data_version)
        self.model_version = str(model_version)
        self.max_area = float(max_area)
        self.timeout = int(timeout)
        if not math.isfinite(self.max_area) or self.max_area <= 0 or self.timeout <= 0:
            raise ValueError("invalid LISFLOOD service settings")
        self.minimum_free_gb = minimum_free_gb
        self.runner = runner
        self._uses_default_runner = runner is generate.run_job
        self.queue: queue.Queue = queue.Queue(maxsize=8)
        self.state: dict[str, dict] = {}
        self.lock = threading.Lock()
        self._work_available = threading.Condition(self.lock)
        self._run_gate = threading.Lock()
        self._worker_thread: threading.Thread | None = None
        self._cleanup_stale_temps()
        if start_worker:
            self._worker_thread = threading.Thread(
                target=self.worker,
                name="lisflood-worker",
                daemon=True,
            )
            self._worker_thread.start()

    @classmethod
    def from_environment(cls, start_worker=True):
        data_dir = Path(os.environ.get("LISFLOOD_DATA_DIR", "/opt/lisflood/data"))
        cache_dir = Path(os.environ.get("LISFLOOD_CACHE_DIR", "/opt/lisflood/cache"))
        engine = Path(os.environ.get("LISFLOOD_ENGINE", "/opt/lisflood/bin/lisflood"))

        checksum_path = data_dir / "SHA256SUMS"
        try:
            checksum_bytes = checksum_path.read_bytes()
            checksum_text = checksum_bytes.decode("ascii")
        except (OSError, UnicodeError) as error:
            raise ValueError("data checksums are unavailable") from error
        checksums: dict[str, str] = {}
        for line in checksum_text.splitlines():
            fields = line.split()
            if not fields:
                continue
            if len(fields) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
                raise ValueError("data checksums are invalid")
            digest, name = fields
            checksums[name] = digest.lower()
        required = {"dem.asc.gz", "population.asc.gz"}
        if not required.issubset(checksums):
            raise ValueError("data checksums are incomplete")
        for name in required:
            path = data_dir / name
            try:
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as error:
                raise ValueError("required data is unavailable") from error
            if actual != checksums[name]:
                raise ValueError("data checksum mismatch")

        dem_path = data_dir / "dem.asc.gz"
        population_path = data_dir / "population.asc.gz"
        try:
            header, dem = generate.read_ascii(dem_path)
            population_header, population = generate.read_ascii(population_path)
            generate.assert_aligned(header, population_header)
        except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
            raise ValueError("data grids are invalid or misaligned") from error
        if dem.shape != population.shape or dem.ndim != 2:
            raise ValueError("data grids are invalid or misaligned")
        dem_values = dem.ravel()
        if not any(math.isfinite(float(value)) for value in dem_values) or any(
            math.isinf(float(value)) for value in dem_values
        ):
            raise ValueError("DEM grid contains invalid values")
        population_values = population.ravel()
        if any(math.isinf(float(value)) for value in population_values):
            raise ValueError("population grid contains invalid values")
        if any(
            math.isfinite(float(value)) and float(value) < 0
            for value in population_values
        ):
            raise ValueError("population grid contains negative values")

        try:
            resolved_engine = generate._resolve_engine(engine)
            detected_model = generate.model_version(resolved_engine)
        except Exception as error:
            raise EngineUnavailable("Engine unavailable") from error

        try:
            max_area = float(os.environ.get("LISFLOOD_MAX_AREA_KM2", "300"))
            timeout = int(os.environ.get("LISFLOOD_JOB_TIMEOUT_SECONDS", "7200"))
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("invalid LISFLOOD service settings") from error
        if not math.isfinite(max_area) or max_area <= 0 or timeout <= 0:
            raise ValueError("invalid LISFLOOD service settings")
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise InsufficientStorage("Insufficient storage") from error
        data_version = hashlib.sha256(checksum_bytes).hexdigest()
        return cls(
            cache_dir,
            resolved_engine,
            header,
            dem,
            population,
            data_version,
            detected_model,
            max_area=max_area,
            timeout=timeout,
            runner=generate.run_job,
            start_worker=start_worker,
        )

    def _grid_shape(self) -> tuple[int, int, float, float, float]:
        try:
            header, ncols, nrows, cell = generate._normalise_grid_header(self.header)
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ValueError("base header is incomplete or invalid") from error
        return ncols, nrows, header["xllcorner"], header["yllcorner"], cell

    def _bounds_for_window(self, window) -> list[list[float]]:
        ncols, nrows, origin_x, origin_y, cell = self._grid_shape()
        try:
            values = tuple(window)
        except TypeError as error:
            raise ValueError("window must contain four integer values") from error
        if len(values) != 4 or any(isinstance(value, bool) or not isinstance(value, Integral) for value in values):
            raise ValueError("window must contain four integer values")
        c0, r0, c1, r1 = (int(value) for value in values)
        if not (0 <= c0 < c1 <= ncols and 0 <= r0 < r1 <= nrows):
            raise ValueError("window is outside the base grid")
        projected = generate.transform_points(
            [
                (origin_x + c0 * cell, origin_y + r0 * cell),
                (origin_x + c0 * cell, origin_y + r1 * cell),
                (origin_x + c1 * cell, origin_y + r0 * cell),
                (origin_x + c1 * cell, origin_y + r1 * cell),
            ],
            "EPSG:32650",
            "EPSG:4326",
        )
        if len(projected) != 4 or any(
            len(point) < 2
            or not math.isfinite(float(point[0]))
            or not math.isfinite(float(point[1]))
            for point in projected
        ):
            raise ValueError("coordinate transform returned invalid points")
        longitudes = [float(point[0]) for point in projected]
        latitudes = [float(point[1]) for point in projected]
        return [
            [float(min(latitudes)), float(min(longitudes))],
            [float(max(latitudes)), float(max(longitudes))],
        ]

    def config(self) -> dict:
        ncols, nrows, _, _, cell = self._grid_shape()
        available = self._bounds_for_window((0, 0, ncols, nrows))
        default = self._bounds_for_window(DEFAULT_WINDOW)
        return {
            "schemaVersion": 1,
            "availableBounds": available,
            "defaultBounds": default,
            "maxAreaKm2": float(self.max_area),
            "gridSizeM": float(cell),
            "returnPeriods": [int(period) for period in RETURN_PERIODS],
            "modelVersion": str(self.model_version),
        }

    def _remove_cache_entry(self, name: str) -> None:
        cache = self.cache_dir.resolve()
        target = self.cache_dir / name
        if target.name != name or target.parent.resolve() != cache:
            return
        try:
            if target.is_symlink() or target.is_file():
                target.unlink(missing_ok=True)
            elif target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        except OSError:
            pass

    def _cleanup_stale_temps(self) -> None:
        try:
            children = tuple(self.cache_dir.iterdir())
        except OSError:
            return
        for child in children:
            if STALE_TEMP_PATTERN.fullmatch(child.name):
                self._remove_cache_entry(child.name)

    def _require_engine(self) -> None:
        if not self._uses_default_runner:
            return
        try:
            generate._resolve_engine(self.engine)
        except Exception as error:
            raise EngineUnavailable("Engine unavailable") from error

    @staticmethod
    def _load_manifest(path: Path) -> dict | None:
        try:
            manifest = json.loads(
                path.read_text(encoding="utf-8"),
                parse_constant=_reject_json_constant,
            )
        except (OSError, UnicodeError, ValueError):
            return None
        return manifest if isinstance(manifest, dict) else None

    def _job_dir(self, identifier: str) -> Path | None:
        cache = self.cache_dir.resolve()
        job_dir = self.cache_dir / identifier
        try:
            if (
                job_dir.name != identifier
                or job_dir.is_symlink()
                or not job_dir.is_dir()
                or job_dir.parent.resolve() != cache
                or job_dir.resolve().parent != cache
            ):
                return None
        except OSError:
            return None
        return job_dir

    @staticmethod
    def _layer_filename(identifier: str, value) -> str | None:
        if not isinstance(value, str):
            return None
        prefix = f"/results/{identifier}/"
        if value.startswith(prefix):
            value = value[len(prefix) :]
        elif value.startswith("/"):
            return None
        if not _safe_filename(value) or not value.lower().endswith(".png"):
            return None
        return value

    def _valid_manifest(self, identifier: str, job_dir: Path, manifest: dict) -> bool:
        schema_version = manifest.get("schemaVersion")
        if isinstance(schema_version, bool) or schema_version != 1:
            return False
        layers = manifest.get("layers")
        if not isinstance(layers, dict) or not layers:
            return False
        for name, value in layers.items():
            if name not in EXPECTED_LAYER_NAMES:
                return False
            filename = self._layer_filename(identifier, value)
            if filename is None:
                return False
            layer = job_dir / filename
            try:
                if layer.is_symlink() or not layer.is_file() or layer.resolve().parent != job_dir.resolve():
                    return False
            except OSError:
                return False
        return True

    def _completed_manifest(self, identifier: str) -> dict | None:
        job_dir = self._job_dir(identifier)
        if job_dir is None:
            return None
        manifest_path = job_dir / "manifest.json"
        try:
            if manifest_path.is_symlink() or not manifest_path.is_file():
                return None
        except OSError:
            return None
        manifest = self._load_manifest(manifest_path)
        return manifest if manifest is not None and self._valid_manifest(identifier, job_dir, manifest) else None

    def _cache_entry_exists(self, identifier: str) -> bool:
        entry = self.cache_dir / identifier
        return entry.exists() or entry.is_symlink()

    def _completed_response_locked(self, identifier: str) -> dict | None:
        manifest = self._completed_manifest(identifier)
        if manifest is None:
            if self._cache_entry_exists(identifier):
                self._remove_cache_entry(identifier)
            return None
        state = self.state.pop(identifier, {})
        response = {
            "jobId": identifier,
            "status": "completed",
            "statusUrl": f"/api/lisflood/jobs/{identifier}",
            "manifestUrl": f"/results/{identifier}/manifest.json",
        }
        if "effectiveBounds" in state:
            response["effectiveBounds"] = state["effectiveBounds"]
        return response

    @staticmethod
    def _failed_response() -> dict:
        return {"status": "failed", "error": "Simulation failed"}

    def _remember_failure_locked(self, identifier: str) -> None:
        self.state[identifier] = self._failed_response()
        while sum(value.get("status") == "failed" for value in self.state.values()) > FAILED_STATE_LIMIT:
            oldest = next(
                key for key, value in self.state.items() if value.get("status") == "failed"
            )
            del self.state[oldest]

    def _state_response(self, identifier: str, state: dict) -> dict:
        if state.get("status") == "failed":
            return self._failed_response()
        response = {
            "jobId": identifier,
            "status": state["status"],
            "statusUrl": f"/api/lisflood/jobs/{identifier}",
            "effectiveBounds": state["effectiveBounds"],
        }
        if state.get("status") == "completed":
            response["manifestUrl"] = f"/results/{identifier}/manifest.json"
        return response

    def submit(self, bounds, period) -> dict:
        period = _normalise_period(period)
        window, effective_bounds = generate.snap_bounds(bounds, self.header, self.max_area)
        window = tuple(int(value) for value in window)
        effective_bounds = [
            [float(coordinate) for coordinate in corner] for corner in effective_bounds
        ]
        identifier = generate.job_id(
            window,
            period,
            self.model_version,
            self.data_version,
        )
        with self.lock:
            completed = self._completed_response_locked(identifier)
            if completed is not None:
                if "effectiveBounds" not in completed:
                    completed["effectiveBounds"] = effective_bounds
                return completed
            existing = self.state.get(identifier)
            if existing is not None and existing.get("status") in {"queued", "running"}:
                return self._state_response(identifier, existing)
            self._require_engine()
            try:
                ensure_cache_space(self.cache_dir, self.minimum_free_gb)
            except InsufficientStorage:
                raise
            except Exception as error:
                raise InsufficientStorage("Insufficient storage") from error
            try:
                self.queue.put_nowait((identifier, window, period, effective_bounds))
            except queue.Full as error:
                raise QueueFull("Queue is full") from error
            self.state[identifier] = {
                "status": "queued",
                "effectiveBounds": effective_bounds,
            }
            self._work_available.notify()
            return self._state_response(identifier, self.state[identifier])

    def status(self, job_id) -> dict:
        if not isinstance(job_id, str) or JOB_ID_PATTERN.fullmatch(job_id) is None:
            raise ValueError("invalid job id")
        with self.lock:
            completed = self._completed_response_locked(job_id)
            if completed is not None:
                state = self.state.get(job_id)
                if state is not None and "effectiveBounds" in state:
                    completed["effectiveBounds"] = state["effectiveBounds"]
                return completed
            state = self.state.get(job_id)
            if state is None:
                raise KeyError("Job not found")
            return self._state_response(job_id, state)

    def _remove_temp(self, target: Path) -> None:
        cache = self.cache_dir.resolve()
        if target.parent.resolve() != cache:
            raise RuntimeError("invalid temporary path")
        self._remove_cache_entry(target.name)

    def _prepare_manifest(self, returned, identifier: str) -> dict:
        if not isinstance(returned, dict):
            raise ValueError("runner did not return a manifest")
        # Round-trip through strict JSON to reject NaN, infinity, and non-JSON values.
        manifest = json.loads(
            json.dumps(returned, allow_nan=False),
            parse_constant=_reject_json_constant,
        )
        layers = manifest.get("layers")
        if not isinstance(layers, dict) or not layers:
            raise ValueError("manifest layers are invalid")
        prefixed = {}
        for name, filename in layers.items():
            if not isinstance(name, str) or not _safe_filename(filename):
                raise ValueError("manifest layer path is invalid")
            prefixed[name] = f"/results/{identifier}/{filename}"
        manifest["layers"] = prefixed
        return manifest

    def _run_next_once(self):
        try:
            item = self.queue.get_nowait()
        except queue.Empty:
            return None
        return self._process_item(item)

    def _process_item(self, item):
        identifier, window, period, effective_bounds = item
        temp = self.cache_dir / f".{identifier}.tmp"
        try:
            with self.lock:
                self.state[identifier] = {
                    "status": "running",
                    "effectiveBounds": effective_bounds,
                }
            if temp.exists() or temp.is_symlink():
                self._remove_temp(temp)
            with self.lock:
                existing = self._completed_response_locked(identifier)
            if existing is not None:
                return existing
            temp.mkdir(parents=True, exist_ok=False)
            returned = self.runner(
                self.engine,
                self.header,
                self.dem,
                self.population,
                window,
                period,
                effective_bounds,
                self.data_version,
                temp,
                self.timeout,
            )
            manifest_path = temp / "manifest.json"
            if manifest_path.exists() or manifest_path.is_symlink():
                if manifest_path.is_symlink():
                    raise ValueError("runner wrote an invalid manifest")
                written = self._load_manifest(manifest_path)
                if written is None:
                    raise ValueError("runner wrote an invalid manifest")
            else:
                written = None
            manifest = self._prepare_manifest(
                written if written is not None else returned,
                identifier,
            )
            (temp / "manifest.json").write_text(
                json.dumps(manifest, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            if not self._valid_manifest(identifier, temp, manifest):
                raise ValueError("manifest files are invalid")
            with self.lock:
                existing = self._completed_response_locked(identifier)
            if existing is not None:
                self._remove_temp(temp)
                return existing
            if self._cache_entry_exists(identifier):
                raise RuntimeError("job cache already exists")
            temp.rename(self.cache_dir / identifier)
            with self.lock:
                self.state.pop(identifier, None)
            return self.status(identifier)
        except Exception:
            try:
                if temp.exists() or temp.is_symlink():
                    self._remove_temp(temp)
            except Exception:
                pass
            with self.lock:
                self._remember_failure_locked(identifier)
            return self._failed_response()
        finally:
            self.queue.task_done()

    def run_next(self):
        with self._run_gate:
            return self._run_next_once()

    def worker(self):
        while True:
            with self._work_available:
                while self.queue.empty():
                    self._work_available.wait()
            self.run_next()


def _safe_value_error(error: ValueError) -> str:
    message = str(error).strip()
    public_messages = {
        "Invalid request",
        "Invalid JSON",
        "JSON body must be an object",
        "Content-Type must be application/json",
        "Content-Length must be valid",
        "Request body is incomplete",
        "bounds is required",
        "returnPeriod is required",
        "return period must be an integer",
        "effective bounds must contain two corners",
        "effective bounds must contain two coordinates per corner",
        "effective bounds coordinate must be numeric",
        "effective bounds coordinate must be finite",
        "effective bounds must be ordered WGS84 coordinates",
        "bounds outside available extent",
    }
    if message in public_messages:
        return message
    if re.fullmatch(r"unsupported return period: -?\d+", message):
        return message
    if re.fullmatch(r"area exceeds [0-9.+eE-]+ km²", message):
        return message
    return "Invalid request"


def make_handler(service: Service):
    """Return a JSON-only request handler bound to ``service``."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            return

        def send_error(self, code, message=None, explain=None):
            self._not_found()

        def _send_json(self, status: int, payload: dict) -> None:
            try:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError):
                status = 500
                body = b'{"error":"Internal service error"}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _not_found(self):
            self._send_json(404, {"error": "Not found"})

        def _read_json(self) -> dict:
            content_type = self.headers.get("Content-Type", "")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                raise ValueError("Content-Type must be application/json")
            content_length = self.headers.get("Content-Length")
            try:
                length = int(content_length) if content_length is not None else -1
            except (TypeError, ValueError):
                raise ValueError("Content-Length must be valid")
            if length < 0:
                raise ValueError("Content-Length must be valid")
            if length > MAX_BODY_BYTES:
                raise OverflowError("Request body too large")
            chunks = []
            remaining = length
            while remaining:
                chunk = self.rfile.read(remaining)
                if not chunk:
                    raise ValueError("Request body is incomplete")
                if len(chunk) > remaining:
                    raise ValueError("Request body is longer than Content-Length")
                chunks.append(chunk)
                remaining -= len(chunk)
            body = b"".join(chunks)
            try:
                value = json.loads(
                    body.decode("utf-8"),
                    parse_constant=_reject_json_constant,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                raise ValueError("Invalid JSON")
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/lisflood/config":
                try:
                    self._send_json(200, service.config())
                except Exception:
                    self._send_json(500, {"error": "Internal service error"})
                return
            prefix = "/api/lisflood/jobs/"
            if path.startswith(prefix):
                identifier = path[len(prefix) :]
                if JOB_ID_PATTERN.fullmatch(identifier) is None:
                    self._send_json(404, {"error": "Job not found"})
                    return
                try:
                    self._send_json(200, service.status(identifier))
                except (KeyError, ValueError):
                    self._send_json(404, {"error": "Job not found"})
                except Exception:
                    self._send_json(500, {"error": "Internal service error"})
                return
            self._not_found()

        def do_POST(self):
            if urlsplit(self.path).path != "/api/lisflood/run":
                self._not_found()
                return
            try:
                payload = self._read_json()
                if "bounds" not in payload:
                    raise ValueError("bounds is required")
                if "returnPeriod" not in payload:
                    raise ValueError("returnPeriod is required")
                if set(payload) - {"bounds", "returnPeriod"}:
                    raise ValueError("Invalid request")
                result = service.submit(payload["bounds"], payload["returnPeriod"])
            except OverflowError:
                self._send_json(413, {"error": "Request body too large"})
            except (socket.timeout, TimeoutError):
                self._send_json(408, {"error": "Request timeout"})
            except QueueFull:
                self._send_json(429, {"error": "Queue is full"})
            except InsufficientStorage:
                self._send_json(507, {"error": "Insufficient storage"})
            except EngineUnavailable:
                self._send_json(503, {"error": "Simulation engine unavailable"})
            except ValueError as error:
                self._send_json(400, {"error": _safe_value_error(error)})
            except Exception:
                self._send_json(500, {"error": "Internal service error"})
            else:
                self._send_json(200 if result.get("status") == "completed" else 202, result)

        def do_HEAD(self):
            self._not_found()

        def do_PUT(self):
            self._not_found()

        def do_DELETE(self):
            self._not_found()

    return Handler


def main() -> None:
    service = Service.from_environment()
    server = ThreadingHTTPServer(("0.0.0.0", 8080), make_handler(service))
    server.serve_forever()


if __name__ == "__main__":
    main()
