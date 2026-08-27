import gzip
import hashlib
import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from lisflood_runner.service import (
    EngineUnavailable,
    InsufficientStorage,
    QueueFull,
    Service,
    make_handler,
)


HEADER = {
    "ncols": 4.0,
    "nrows": 4.0,
    "xllcorner": 500000.0,
    "yllcorner": 3500000.0,
    "cellsize": 30.0,
}


def manifest_for(*, layer="depth.png", **extra):
    manifest = {
        "schemaVersion": 1,
        "layers": {"depth": layer},
        "stats": {"maximumDepthM": 1.0},
    }
    manifest.update(extra)
    return manifest


def write_manifest(path: Path, manifest: dict) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def make_service(cache: Path, *, runner=None, start_worker=False, **kwargs) -> Service:
    return Service(
        cache,
        Path("engine"),
        HEADER,
        np.ones((4, 4)),
        np.ones((4, 4)),
        "data",
        "model",
        runner=runner or (lambda *args: manifest_for()),
        start_worker=start_worker,
        **kwargs,
    )


class HTTPTestCase(unittest.TestCase):
    def start_server(self, service: Service):
        server = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(service)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        return server

    def request(self, server, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        self.addCleanup(connection.close)
        encoded = None if body is None else body.encode("utf-8")
        connection.request(method, path, body=encoded, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        return response.status, response.getheader("Content-Type"), json.loads(payload)


class ServiceSubmissionTests(unittest.TestCase):
    def test_identical_snapped_jobs_share_id_and_queue_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            service = Service(
                cache,
                Path("engine"),
                {"ncols": 4.0, "nrows": 4.0, "xllcorner": 0.0, "yllcorner": 0.0, "cellsize": 1.0},
                np.ones((4, 4)),
                np.ones((4, 4)),
                "data",
                "model",
                max_area=300,
                start_worker=False,
            )

            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                first = service.submit([[1, 2], [3, 4]], 20)
                second = service.submit([[1, 2], [3, 4]], 20)

            self.assertEqual(first["jobId"], second["jobId"])
            self.assertEqual(first["status"], "queued")
            self.assertEqual(second["status"], "queued")
            self.assertEqual(service.queue.qsize(), 1)

    def test_completed_disk_manifest_wins_without_queueing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                service = make_service(cache)
                first = service.submit([[1, 2], [3, 4]], 20)
                write_manifest(cache / first["jobId"], manifest_for())
                service.state[first["jobId"]] = {"status": "failed", "error": "Simulation failed"}
                second = service.submit([[1, 2], [3, 4]], 20)

            self.assertEqual(second["status"], "completed")
            self.assertEqual(second["manifestUrl"], f"/results/{first['jobId']}/manifest.json")
            self.assertEqual(service.queue.qsize(), 1)

    def test_ninth_distinct_pending_job_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory), minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                side_effect=[((index, 0, index + 1, 1), [[1.0, float(index)], [2.0, float(index + 1)]]) for index in range(9)],
            ):
                for period in [5, 10, 20, 50, 100, 5, 10, 20, 50]:
                    if service.queue.qsize() < 8:
                        service.submit([[1, 2], [3, 4]], period)
                    else:
                        with self.assertRaises(QueueFull):
                            service.submit([[1, 2], [3, 4]], period)
            self.assertEqual(service.queue.qsize(), 8)

    def test_low_disk_is_rejected_before_queueing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory), minimum_free_gb=15)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ), mock.patch(
                "lisflood_runner.service.shutil.disk_usage",
                return_value=shutil.disk_usage(directory)._replace(free=0),
            ):
                with self.assertRaises(InsufficientStorage):
                    service.submit([[1, 2], [3, 4]], 20)
            self.assertEqual(service.queue.qsize(), 0)


class ServiceExecutionTests(unittest.TestCase):
    def test_run_next_executes_one_fifo_job_and_atomically_publishes_manifest(self) -> None:
        calls = []

        def runner(*args):
            calls.append(args[4:7])
            staging = args[8]
            (staging / "depth.png").write_bytes(b"png")
            return manifest_for()

        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory), runner=runner, minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                side_effect=[
                    ((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
                    ((1, 0, 3, 2), [[1.0, 2.0], [3.0, 4.0]]),
                ],
            ):
                first = service.submit([[1, 2], [3, 4]], 5)
                second = service.submit([[1, 2], [3, 4]], 10)
            self.assertEqual(service.run_next()["status"], "completed")
            self.assertEqual(service.queue.qsize(), 1)
            self.assertEqual(service.status(first["jobId"])["status"], "completed")
            self.assertEqual(service.status(second["jobId"])["status"], "queued")
            self.assertEqual(len(calls), 1)
            self.assertFalse((Path(directory) / f".{first['jobId']}.tmp").exists())
            self.assertTrue((Path(directory) / first["jobId"] / "manifest.json").is_file())
            saved = json.loads((Path(directory) / first["jobId"] / "manifest.json").read_text())
            self.assertEqual(saved["layers"]["depth"], f"/results/{first['jobId']}/depth.png")

    def test_failed_job_cleans_temp_and_hides_runner_error(self) -> None:
        def runner(*args):
            (args[8] / "secret.txt").write_text("secret", encoding="utf-8")
            raise RuntimeError("secret/path should not be exposed")

        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory), runner=runner, minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                submitted = service.submit([[1, 2], [3, 4]], 20)
            result = service.run_next()
            self.assertEqual(result, {"status": "failed", "error": "Simulation failed"})
            self.assertEqual(service.status(submitted["jobId"]), result)
            self.assertFalse((Path(directory) / f".{submitted['jobId']}.tmp").exists())


class ServiceHTTPTests(HTTPTestCase):
    def test_config_exposes_contract_and_wgs84_bounds(self) -> None:
        header = dict(HEADER, ncols=1498.0, nrows=825.0)
        with tempfile.TemporaryDirectory() as directory:
            service = Service(
                Path(directory),
                Path("engine"),
                header,
                np.ones((1, 1)),
                np.ones((1, 1)),
                "data",
                "8.0.3 ACC",
                start_worker=False,
                minimum_free_gb=0,
            )
            with mock.patch(
                "lisflood_runner.service.generate.transform_points",
                return_value=[
                    (118.0, 31.0),
                    (118.0, 31.1),
                    (118.2, 31.0),
                    (118.2, 31.1),
                ],
            ):
                server = self.start_server(service)
                status, content_type, payload = self.request(server, "GET", "/api/lisflood/config")
            self.assertEqual(status, 200)
            self.assertEqual(content_type, "application/json")
            self.assertEqual(payload["schemaVersion"], 1)
            self.assertEqual(payload["returnPeriods"], [5, 10, 20, 50, 100])
            self.assertEqual(payload["maxAreaKm2"], 300.0)
            self.assertEqual(payload["modelVersion"], "8.0.3 ACC")
            self.assertEqual(payload["availableBounds"], [[31.0, 118.0], [31.1, 118.2]])
            self.assertEqual(payload["defaultBounds"], [[31.0, 118.0], [31.1, 118.2]])

    def test_invalid_json_content_type_size_period_and_bounds_are_client_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory), minimum_free_gb=0)
            server = self.start_server(service)
            base_headers = {"Content-Type": "application/json"}
            status, _, payload = self.request(server, "POST", "/api/lisflood/run", "{", base_headers)
            self.assertEqual(status, 400)
            self.assertEqual(payload["error"], "Invalid JSON")
            status, _, payload = self.request(
                server,
                "POST",
                "/api/lisflood/run",
                json.dumps({"bounds": [[1, 2], [3, 4]], "period": 20}),
                {"Content-Type": "text/plain"},
            )
            self.assertEqual(status, 400)
            self.assertIn("Content-Type", payload["error"])
            status, _, payload = self.request(
                server,
                "POST",
                "/api/lisflood/run",
                "x" * (4097),
                base_headers,
            )
            self.assertEqual(status, 413)
            self.assertEqual(payload["error"], "Request body too large")
            status, _, payload = self.request(
                server,
                "POST",
                "/api/lisflood/run",
                json.dumps({"bounds": [[1, 2], [3, 4]], "period": True}),
                base_headers,
            )
            self.assertEqual(status, 400)
            self.assertIn("period", payload["error"])
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                side_effect=ValueError("bounds are invalid"),
            ):
                status, _, payload = self.request(
                    server,
                    "POST",
                    "/api/lisflood/run",
                    json.dumps({"bounds": [[1, 2], [3, 4]], "period": 20}),
                    base_headers,
                )
            self.assertEqual(status, 400)
            self.assertIn("bounds", payload["error"])

    def test_post_run_and_get_completed_job(self) -> None:
        def runner(*args):
            (args[8] / "depth.png").write_bytes(b"png")
            return manifest_for()

        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory), runner=runner, minimum_free_gb=0)
            server = self.start_server(service)
            headers = {"Content-Type": "application/json; charset=utf-8"}
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                status, content_type, submitted = self.request(
                    server,
                    "POST",
                    "/api/lisflood/run",
                    json.dumps({"bounds": [[1, 2], [3, 4]], "period": 20}),
                    headers,
                )
            self.assertEqual(status, 202)
            self.assertEqual(content_type, "application/json")
            self.assertEqual(submitted["status"], "queued")
            service.run_next()
            status, _, completed = self.request(
                server,
                "GET",
                f"/api/lisflood/jobs/{submitted['jobId']}",
            )
            self.assertEqual(status, 200)
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["manifestUrl"], f"/results/{submitted['jobId']}/manifest.json")

    def test_unknown_and_malformed_jobs_are_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server = self.start_server(make_service(Path(directory)))
            for identifier in ("0123456789abcdef0124", "bad", "0123456789ABCDEF0123"):
                status, _, payload = self.request(
                    server,
                    "GET",
                    f"/api/lisflood/jobs/{identifier}",
                )
                self.assertEqual(status, 404)
                self.assertIn(payload["error"], {"Job not found", "Not found"})

    def test_exception_mappings_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory))
            server = self.start_server(service)
            body = json.dumps({"bounds": [[1, 2], [3, 4]], "period": 20})
            for exception, expected_status, expected_error in (
                (QueueFull("secret"), 429, "Queue is full"),
                (InsufficientStorage("secret"), 507, "Insufficient storage"),
                (EngineUnavailable("secret"), 503, "Engine unavailable"),
                (RuntimeError("secret/path"), 500, "Internal service error"),
            ):
                with self.subTest(exception=type(exception).__name__), mock.patch.object(
                    service, "submit", side_effect=exception
                ):
                    status, _, payload = self.request(
                        server,
                        "POST",
                        "/api/lisflood/run",
                        body,
                        {"Content-Type": "application/json"},
                    )
                self.assertEqual(status, expected_status)
                self.assertEqual(payload["error"], expected_error)
                self.assertNotIn("secret", json.dumps(payload))

    def test_restart_reconstructs_completed_status_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            service = make_service(cache)
            identifier = "0123456789abcdef0123"
            write_manifest(cache / identifier, manifest_for())
            restarted = make_service(cache)
            self.assertEqual(restarted.status(identifier)["status"], "completed")
            self.assertEqual(restarted.status(identifier)["manifestUrl"], f"/results/{identifier}/manifest.json")

    def test_stale_temp_is_removed_and_existing_completed_cache_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            calls = []

            def runner(*args):
                calls.append(True)
                (args[8] / "depth.png").write_bytes(b"new")
                return manifest_for()

            service = make_service(cache, runner=runner, minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                submitted = service.submit([[1, 2], [3, 4]], 20)
            identifier = submitted["jobId"]
            (cache / f".{identifier}.tmp").mkdir()
            (cache / f".{identifier}.tmp" / "old").write_text("old", encoding="utf-8")
            write_manifest(cache / identifier, manifest_for())
            (cache / identifier / "depth.png").write_bytes(b"old")
            self.assertEqual(service.run_next()["status"], "completed")
            self.assertEqual(calls, [])
            self.assertEqual((cache / identifier / "depth.png").read_bytes(), b"old")
            self.assertFalse((cache / f".{identifier}.tmp").exists())

    def test_manifest_traversal_is_rejected_and_cleaned(self) -> None:
        def runner(*args):
            return manifest_for(layer="../secret.png")

        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory), runner=runner, minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                submitted = service.submit([[1, 2], [3, 4]], 20)
            self.assertEqual(service.run_next(), {"status": "failed", "error": "Simulation failed"})
            self.assertFalse((Path(directory) / f".{submitted['jobId']}.tmp").exists())

    def test_concurrent_run_next_calls_have_one_active_runner(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        active = 0
        peak = 0
        active_lock = threading.Lock()

        def runner(*args):
            nonlocal active, peak
            with active_lock:
                active += 1
                peak = max(peak, active)
            entered.set()
            release.wait(2)
            with active_lock:
                active -= 1
            return manifest_for()

        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory), runner=runner, minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                side_effect=[
                    ((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
                    ((1, 0, 3, 2), [[1.0, 2.0], [3.0, 4.0]]),
                ],
            ):
                service.submit([[1, 2], [3, 4]], 5)
                service.submit([[1, 2], [3, 4]], 10)
            first = threading.Thread(target=service.run_next)
            second = threading.Thread(target=service.run_next)
            first.start()
            self.assertTrue(entered.wait(1))
            second.start()
            time.sleep(0.05)
            self.assertEqual(peak, 1)
            release.set()
            first.join(2)
            second.join(2)
            self.assertEqual(peak, 1)


class EnvironmentTests(unittest.TestCase):
    @staticmethod
    def write_grid(path: Path, values: str) -> None:
        content = (
            "ncols 2\n"
            "nrows 2\n"
            "xllcorner 500000\n"
            "yllcorner 3500000\n"
            "cellsize 30\n"
            "NODATA_value -9999\n"
            f"{values}\n"
        ).encode("ascii")
        with gzip.GzipFile(filename=path, mode="wb", mtime=0) as target:
            target.write(content)

    def test_from_environment_verifies_data_and_engine_and_builds_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            cache_dir = root / "cache"
            data_dir.mkdir()
            self.write_grid(data_dir / "dem.asc.gz", "1 2\n3 4")
            self.write_grid(data_dir / "population.asc.gz", "0 1\n2 3")
            lines = []
            for name in ("dem.asc.gz", "population.asc.gz"):
                lines.append(f"{hashlib.sha256((data_dir / name).read_bytes()).hexdigest()}  {name}")
            checksum_bytes = ("\n".join(lines) + "\n").encode("ascii")
            (data_dir / "SHA256SUMS").write_bytes(checksum_bytes)
            engine = root / "lisflood"
            engine.write_text("#!/bin/sh\necho 'LISFLOOD-FP version 8.0.3 (double)'\n", encoding="utf-8")
            engine.chmod(0o755)
            environment = {
                "LISFLOOD_DATA_DIR": str(data_dir),
                "LISFLOOD_CACHE_DIR": str(cache_dir),
                "LISFLOOD_ENGINE": str(engine),
                "LISFLOOD_MAX_AREA_KM2": "12.5",
                "LISFLOOD_JOB_TIMEOUT_SECONDS": "99",
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                service = Service.from_environment(start_worker=False)
            self.assertEqual(service.max_area, 12.5)
            self.assertEqual(service.timeout, 99)
            self.assertEqual(service.model_version, "8.0.3 ACC")
            self.assertEqual(service.data_version, hashlib.sha256(checksum_bytes).hexdigest())
            self.assertTrue(cache_dir.is_dir())

            (data_dir / "dem.asc.gz").write_bytes(b"tampered")
            with mock.patch.dict(os.environ, environment, clear=False):
                with self.assertRaises(ValueError):
                    Service.from_environment(start_worker=False)

    def test_from_environment_maps_missing_or_unusable_engine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            data_dir.mkdir()
            self.write_grid(data_dir / "dem.asc.gz", "1 2\n3 4")
            self.write_grid(data_dir / "population.asc.gz", "0 1\n2 3")
            lines = [
                f"{hashlib.sha256((data_dir / name).read_bytes()).hexdigest()}  {name}"
                for name in ("dem.asc.gz", "population.asc.gz")
            ]
            (data_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii")
            environment = {
                "LISFLOOD_DATA_DIR": str(data_dir),
                "LISFLOOD_CACHE_DIR": str(root / "cache"),
                "LISFLOOD_ENGINE": str(root / "missing-engine"),
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                with self.assertRaises(EngineUnavailable):
                    Service.from_environment(start_worker=False)


if __name__ == "__main__":
    unittest.main()
