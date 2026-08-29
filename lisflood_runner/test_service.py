import gzip
import hashlib
import http.client
import io
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

LAYER_NAMES = ("dem", "population", "depth", "hazard", "risk")


def manifest_for(*, layer=None, job_id=None, **extra):
    layers = {name: f"{name}.png" for name in LAYER_NAMES}
    if layer is not None:
        layers["depth"] = layer
    if job_id is not None:
        layers = {name: f"/results/{job_id}/{filename}" for name, filename in layers.items()}
    manifest = {
        "schemaVersion": 1,
        "generatedAt": "2026-01-01T00:00:00+00:00",
        "modelVersion": "model",
        "dataVersion": "data",
        "returnPeriod": 20,
        "rainfallMm": 10.0,
        "bounds": [[1.0, 2.0], [3.0, 4.0]],
        "populationBreaks": [0.0, 1.0, 2.0],
        "layers": layers,
        "stats": {
            "floodedAreaKm2": 1.0,
            "exposedPopulation": 2.0,
            "maximumDepthM": 1.0,
        },
    }
    manifest.update(extra)
    return manifest


def write_manifest(path: Path, manifest: dict) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for filename in manifest.get("layers", {}).values():
        if isinstance(filename, str) and filename.endswith(".png"):
            (path / Path(filename).name).write_bytes(b"png")
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def runner_manifest(staging: Path, manifest: dict | None = None, *, depth=b"png") -> dict:
    if manifest is None:
        manifest = manifest_for()
    write_manifest(staging, manifest)
    if depth != b"png":
        (staging / "depth.png").write_bytes(depth)
    return manifest


def make_service(cache: Path, *, runner=None, start_worker=False, **kwargs) -> Service:
    if runner is None:
        runner = lambda *args: runner_manifest(args[8])
    return Service(
        cache,
        Path("engine"),
        HEADER,
        np.ones((4, 4)),
        np.ones((4, 4)),
        "data",
        "model",
        runner=runner,
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
                runner=lambda *args: manifest_for(),
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
                write_manifest(cache / first["jobId"], manifest_for(job_id=first["jobId"]))
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
            return runner_manifest(args[8])

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
            with self.assertLogs("lisflood_runner.service", level="ERROR") as logs:
                result = service.run_next()
            self.assertEqual(result, {"status": "failed", "error": "Simulation failed"})
            self.assertEqual(service.status(submitted["jobId"]), result)
            public_response = json.dumps(result)
            self.assertNotIn("secret/path", public_response)
            self.assertNotIn("secret/path", json.dumps(service.status(submitted["jobId"])))
            log_output = "\n".join(logs.output)
            self.assertIn(submitted["jobId"], log_output)
            self.assertIn("20", log_output)
            self.assertIn("secret/path should not be exposed", log_output)
            self.assertIn("Traceback", log_output)
            self.assertFalse((Path(directory) / f".{submitted['jobId']}.tmp").exists())

    def test_worker_blocks_idle_and_processes_later_submission(self) -> None:
        processed = threading.Event()

        def runner(*args):
            processed.set()
            return runner_manifest(args[8])

        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory), runner=runner, start_worker=False, minimum_free_gb=0)
            original_wait = service._work_available.wait
            waiting = threading.Event()

            def observed_wait(*args, **kwargs):
                waiting.set()
                return original_wait(*args, **kwargs)

            service._work_available.wait = observed_wait
            worker = threading.Thread(target=service.worker, daemon=True)
            worker.start()
            service._worker_thread = worker
            self.assertTrue(worker.daemon)
            self.assertTrue(worker.is_alive())
            self.assertTrue(waiting.wait(1))
            self.assertFalse(processed.is_set())
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                submitted = service.submit([[1, 2], [3, 4]], 20)
            self.assertTrue(processed.wait(2))
            service.queue.join()
            self.assertEqual(service.status(submitted["jobId"])["status"], "completed")

    def test_concurrent_run_next_gates_before_dequeue_and_preserves_fifo(self) -> None:
        calls = []
        calls_lock = threading.Lock()
        first_get = threading.Event()
        release_first_get = threading.Event()
        first_runner = threading.Event()
        release_first_runner = threading.Event()
        second_ready = threading.Event()
        events = []
        events_lock = threading.Lock()

        def runner(*args):
            period = args[5]
            with calls_lock:
                calls.append(period)
            runner_manifest(args[8])
            if period == 5:
                first_runner.set()
                self.assertTrue(release_first_runner.wait(2))
            return manifest_for()

        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory), runner=runner, minimum_free_gb=0)
            original_gate = service._run_gate

            class RecordingGate:
                def __enter__(self):
                    with events_lock:
                        events.append(("gate", threading.current_thread().name))
                    original_gate.acquire()
                    return self

                def __exit__(self, *args):
                    original_gate.release()

            service._run_gate = RecordingGate()
            original_get = service.queue.get_nowait

            def observed_get():
                item = original_get()
                with events_lock:
                    events.append(("get", threading.current_thread().name))
                if threading.current_thread().name == "job1":
                    first_get.set()
                    self.assertTrue(release_first_get.wait(2))
                return item

            service.queue.get_nowait = observed_get
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                side_effect=[
                    ((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
                    ((1, 0, 3, 2), [[1.0, 2.0], [3.0, 4.0]]),
                ],
            ):
                service.submit([[1, 2], [3, 4]], 5)
                service.submit([[1, 2], [3, 4]], 10)

            first_thread = threading.Thread(target=service.run_next, name="job1")

            def run_second():
                second_ready.set()
                service.run_next()

            second_thread = threading.Thread(target=run_second, name="job2")
            first_thread.start()
            self.assertTrue(first_get.wait(2))
            second_thread.start()
            self.assertTrue(second_ready.wait(2))
            release_first_get.set()
            self.assertTrue(first_runner.wait(2))
            with calls_lock:
                self.assertEqual(calls, [5])
            self.assertFalse(release_first_runner.is_set())
            release_first_runner.set()
            first_thread.join(2)
            second_thread.join(2)
            self.assertFalse(first_thread.is_alive())
            self.assertFalse(second_thread.is_alive())
            self.assertEqual(calls, [5, 10])
            first_gate = events.index(("gate", "job1"))
            first_dequeue = events.index(("get", "job1"))
            self.assertLess(first_gate, first_dequeue)

    def test_default_runner_engine_disappearance_is_rejected_before_queueing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / "lisflood"
            engine.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            engine.chmod(0o755)
            service = Service(
                root / "cache",
                engine,
                HEADER,
                np.ones((4, 4)),
                np.ones((4, 4)),
                "data",
                "model",
                minimum_free_gb=0,
                start_worker=False,
            )
            engine.unlink()
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                with self.assertRaises(EngineUnavailable):
                    service.submit([[1, 2], [3, 4]], 20)
            self.assertEqual(service.queue.qsize(), 0)

    def test_completed_state_is_removed_after_disk_publish(self) -> None:
        def runner(*args):
            return runner_manifest(args[8])

        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory), runner=runner, minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                submitted = service.submit([[1, 2], [3, 4]], 20)
            self.assertEqual(service.run_next()["status"], "completed")
            self.assertNotIn(submitted["jobId"], service.state)

    def test_failed_state_is_bounded_without_evicting_newest(self) -> None:
        def runner(*args):
            raise RuntimeError("failure")

        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory), runner=runner, minimum_free_gb=0)
            windows = [((index, 0, index + 1, 1), [[1.0, 2.0], [3.0, 4.0]]) for index in range(70)]
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                side_effect=windows,
            ), mock.patch("lisflood_runner.service.logging.getLogger"):
                submitted = []
                for _ in range(70):
                    submitted.append(service.submit([[1, 2], [3, 4]], 20))
                    service.run_next()
            failed = [value for value in service.state.values() if value.get("status") == "failed"]
            self.assertLessEqual(len(failed), 64)
            self.assertEqual(service.status(submitted[-1]["jobId"])["status"], "failed")

    def test_invalid_final_manifest_is_discarded_and_job_can_publish(self) -> None:
        def runner(*args):
            return runner_manifest(args[8], depth=b"new")

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            service = make_service(cache, runner=runner, minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                identifier = service.submit([[1, 2], [3, 4]], 20)["jobId"]
            write_manifest(cache / identifier, manifest_for(job_id=identifier, schemaVersion=2))
            self.assertEqual(service.status(identifier)["status"], "queued")
            self.assertEqual(service.run_next()["status"], "completed")
            self.assertEqual(json.loads((cache / identifier / "manifest.json").read_text())["schemaVersion"], 1)

    def test_partial_final_manifest_is_discarded_and_requeued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            service = make_service(cache, minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                identifier = service.submit([[1, 2], [3, 4]], 20)["jobId"]
            partial = manifest_for(job_id=identifier)
            partial["layers"].pop("risk")
            write_manifest(cache / identifier, partial)
            self.assertEqual(service.status(identifier)["status"], "queued")
            self.assertFalse((cache / identifier).exists())
            self.assertEqual(service.run_next()["status"], "completed")

    def test_relative_final_manifest_is_discarded_and_requeued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            service = make_service(cache, minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                identifier = service.submit([[1, 2], [3, 4]], 20)["jobId"]
            write_manifest(cache / identifier, manifest_for())
            self.assertEqual(service.status(identifier)["status"], "queued")
            self.assertFalse((cache / identifier).exists())
            self.assertEqual(service.run_next()["status"], "completed")

    def test_final_manifest_requires_production_result_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            service = make_service(cache, minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                identifier = service.submit([[1, 2], [3, 4]], 20)["jobId"]
            incomplete = manifest_for(job_id=identifier)
            del incomplete["populationBreaks"]
            write_manifest(cache / identifier, incomplete)
            self.assertEqual(service.status(identifier)["status"], "queued")
            self.assertFalse((cache / identifier).exists())

    def test_missing_or_unsafe_layer_cache_is_not_completed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            service = make_service(cache, minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                identifier = service.submit([[1, 2], [3, 4]], 20)["jobId"]
            write_manifest(cache / identifier, manifest_for(job_id=identifier))
            (cache / identifier / "depth.png").unlink()
            self.assertEqual(service.status(identifier)["status"], "queued")
            self.assertFalse((cache / identifier).exists())

            write_manifest(cache / identifier, manifest_for(job_id=identifier, layer="../secret.png"))
            self.assertEqual(service.status(identifier)["status"], "queued")
            self.assertFalse((cache / identifier).exists())

    def test_symlinked_job_cache_is_unlinked_without_following_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            outside = root / "outside"
            outside.mkdir()
            write_manifest(outside, manifest_for())
            service = make_service(cache, minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                identifier = service.submit([[1, 2], [3, 4]], 20)["jobId"]
            try:
                (cache / identifier).symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            self.assertEqual(service.status(identifier)["status"], "queued")
            self.assertFalse((cache / identifier).exists())
            self.assertTrue((outside / "manifest.json").is_file())

    def test_initialization_cleans_only_exact_stale_temp_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            exact = cache / ".0123456789abcdef0123.tmp"
            exact.mkdir()
            (exact / "old").write_text("old", encoding="utf-8")
            unrelated = cache / ".not-a-job.tmp"
            unrelated.mkdir()
            short = cache / ".0123.tmp"
            short.mkdir()
            make_service(cache, minimum_free_gb=0)
            self.assertFalse(exact.exists())
            self.assertTrue(unrelated.is_dir())
            self.assertTrue(short.is_dir())


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
            self.assertEqual(payload["gridSizeM"], 30.0)
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
                json.dumps({"bounds": [[1, 2], [3, 4]], "returnPeriod": 20}),
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
                json.dumps({"bounds": [[1, 2], [3, 4]], "returnPeriod": True}),
                base_headers,
            )
            self.assertEqual(status, 400)
            self.assertIn("period", payload["error"])
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                side_effect=ValueError("bounds outside available extent"),
            ):
                status, _, payload = self.request(
                    server,
                    "POST",
                    "/api/lisflood/run",
                    json.dumps({"bounds": [[1, 2], [3, 4]], "returnPeriod": 20}),
                    base_headers,
                )
            self.assertEqual(status, 400)
            self.assertEqual(payload["error"], "bounds outside available extent")

    def test_incomplete_request_body_is_rejected_and_timeout_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory))
            handler_class = make_handler(service)
            handler = handler_class.__new__(handler_class)
            handler.headers = {
                "Content-Type": "application/json",
                "Content-Length": "4",
            }
            handler.rfile = io.BytesIO(b"{}")
            with self.assertRaisesRegex(ValueError, "incomplete"):
                handler._read_json()

            handler.path = "/api/lisflood/run"
            handler._read_json = mock.Mock(side_effect=TimeoutError("secret timeout"))
            handler._send_json = mock.Mock()
            handler_class.do_POST(handler)
            handler._send_json.assert_called_once_with(408, {"error": "Request timeout"})

    def test_post_run_and_get_completed_job(self) -> None:
        def runner(*args):
            return runner_manifest(args[8])

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
                    json.dumps({"bounds": [[1, 2], [3, 4]], "returnPeriod": 20}),
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

    def test_default_runner_engine_disappearance_maps_to_http_503(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / "lisflood"
            engine.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            engine.chmod(0o755)
            service = Service(
                root / "cache",
                engine,
                HEADER,
                np.ones((4, 4)),
                np.ones((4, 4)),
                "data",
                "model",
                minimum_free_gb=0,
                start_worker=False,
            )
            server = self.start_server(service)
            engine.unlink()
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                status, _, payload = self.request(
                    server,
                    "POST",
                    "/api/lisflood/run",
                    json.dumps({"bounds": [[1, 2], [3, 4]], "returnPeriod": 20}),
                    {"Content-Type": "application/json"},
                )
            self.assertEqual(status, 503)
            self.assertEqual(payload, {"error": "Simulation engine unavailable"})
            self.assertEqual(service.queue.qsize(), 0)

    def test_cached_completed_post_returns_200(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory), minimum_free_gb=0)
            server = self.start_server(service)
            body = {"bounds": [[1, 2], [3, 4]], "returnPeriod": 20}
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                first_status, _, first = self.request(
                    server,
                    "POST",
                    "/api/lisflood/run",
                    json.dumps(body),
                    {"Content-Type": "application/json"},
                )
                write_manifest(
                    Path(directory) / first["jobId"],
                    manifest_for(job_id=first["jobId"]),
                )
                second_status, _, second = self.request(
                    server,
                    "POST",
                    "/api/lisflood/run",
                    json.dumps(body),
                    {"Content-Type": "application/json"},
                )
            self.assertEqual(first_status, 202)
            self.assertEqual(second_status, 200)
            self.assertEqual(second["status"], "completed")
            self.assertEqual(service.queue.qsize(), 1)

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
            body = json.dumps({"bounds": [[1, 2], [3, 4]], "returnPeriod": 20})
            for exception, expected_status, expected_error in (
                (QueueFull("secret"), 429, "Queue is full"),
                (InsufficientStorage("secret"), 507, "Insufficient storage"),
                (EngineUnavailable("secret"), 503, "Simulation engine unavailable"),
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

    def test_unexpected_value_error_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory))
            server = self.start_server(service)
            with mock.patch.object(
                service,
                "submit",
                side_effect=ValueError("private secret /srv/cache/job-123"),
            ):
                status, _, payload = self.request(
                    server,
                    "POST",
                    "/api/lisflood/run",
                    json.dumps({"bounds": [[1, 2], [3, 4]], "returnPeriod": 20}),
                    {"Content-Type": "application/json"},
                )
            self.assertEqual(status, 400)
            self.assertEqual(payload, {"error": "Invalid request"})

    def test_period_alias_is_not_accepted_by_http_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory))
            server = self.start_server(service)
            status, _, payload = self.request(
                server,
                "POST",
                "/api/lisflood/run",
                json.dumps({"bounds": [[1, 2], [3, 4]], "period": 20}),
                {"Content-Type": "application/json"},
            )
            self.assertEqual(status, 400)
            self.assertEqual(payload, {"error": "returnPeriod is required"})

    def test_area_limit_validation_message_is_public(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = make_service(Path(directory))
            server = self.start_server(service)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                side_effect=ValueError("area exceeds 300 km²"),
            ):
                status, _, payload = self.request(
                    server,
                    "POST",
                    "/api/lisflood/run",
                    json.dumps({"bounds": [[1, 2], [3, 4]], "returnPeriod": 20}),
                    {"Content-Type": "application/json"},
                )
            self.assertEqual(status, 400)
            self.assertEqual(payload, {"error": "area exceeds 300 km²"})

    def test_restart_reconstructs_completed_status_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            service = make_service(cache)
            identifier = "0123456789abcdef0123"
            write_manifest(cache / identifier, manifest_for(job_id=identifier))
            restarted = make_service(cache)
            self.assertEqual(restarted.status(identifier)["status"], "completed")
            self.assertEqual(restarted.status(identifier)["manifestUrl"], f"/results/{identifier}/manifest.json")

    def test_stale_temp_is_removed_and_existing_completed_cache_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            calls = []

            def runner(*args):
                calls.append(True)
                return runner_manifest(args[8], depth=b"new")

            service = make_service(cache, runner=runner, minimum_free_gb=0)
            with mock.patch(
                "lisflood_runner.service.generate.snap_bounds",
                return_value=((0, 0, 2, 2), [[1.0, 2.0], [3.0, 4.0]]),
            ):
                submitted = service.submit([[1, 2], [3, 4]], 20)
            identifier = submitted["jobId"]
            (cache / f".{identifier}.tmp").mkdir()
            (cache / f".{identifier}.tmp" / "old").write_text("old", encoding="utf-8")
            write_manifest(cache / identifier, manifest_for(job_id=identifier))
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
            return runner_manifest(args[8])

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
