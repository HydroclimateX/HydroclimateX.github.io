import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import numpy as np

from lisflood_runner import generate
from lisflood_runner.generate import (
    HAZARD_SUFFIX,
    RETURN_PERIODS,
    build_risk,
    crop_grid,
    design_storm,
    job_id,
    mass_balance_error,
    model_version,
    run_job,
    snap_bounds,
    write_ascii,
    write_rainfall,
)


class WindowTests(unittest.TestCase):
    HEADER = {
        "ncols": 10.0, "nrows": 10.0, "xllcorner": 500000.0,
        "yllcorner": 3500000.0, "cellsize": 30.0, "nodata_value": -9999.0,
    }

    def test_snap_crop_and_stable_job_id(self) -> None:
        with unittest.mock.patch(
            "lisflood_runner.generate.transform_points",
            return_value=[
                (500030.0, 3500030.0),
                (500180.0, 3500180.0),
                (500030.0, 3500030.0),
                (500180.0, 3500180.0),
            ],
        ):
            window, effective = snap_bounds([[31.0, 118.0], [31.1, 118.1]], self.HEADER, 300)
        self.assertEqual(window, (1, 1, 6, 6))
        self.assertEqual(effective, [[3500030.0, 500030.0], [3500180.0, 500180.0]])
        grid = np.arange(100).reshape(10, 10)
        np.testing.assert_array_equal(crop_grid(grid, window), grid[4:9, 1:6])
        self.assertEqual(job_id(window, 20, "8.0.3", "data"), job_id(window, 20, "8.0.3", "data"))

    def test_surface_v2_parameter_version_invalidates_surface_v1_job_ids(self) -> None:
        window = (1, 1, 6, 6)
        old_value = "1,1,6,6|20|8.0.3|surface-v1|data"
        new_value = "1,1,6,6|20|8.0.3|surface-v2|data"
        self.assertEqual(generate.PARAMETER_VERSION, "surface-v2")
        self.assertEqual(job_id(window, 20, "8.0.3", "data"), hashlib.sha256(new_value.encode()).hexdigest()[:20])
        self.assertNotEqual(job_id(window, 20, "8.0.3", "data"), hashlib.sha256(old_value.encode()).hexdigest()[:20])

    def test_rejects_area_above_limit(self) -> None:
        large_header = dict(self.HEADER, ncols=1000.0, nrows=1000.0)
        with unittest.mock.patch(
            "lisflood_runner.generate.transform_points",
            return_value=[
                (500000.0, 3500000.0),
                (500000.0, 3520000.0),
                (520000.0, 3500000.0),
                (520000.0, 3520000.0),
            ],
        ):
            with self.assertRaisesRegex(ValueError, "300"):
                snap_bounds([[31.0, 118.0], [31.2, 118.2]], large_header, 300)

    def test_write_ascii_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grid.asc"
            data = np.arange(9, dtype=float).reshape(3, 3)
            header = dict(self.HEADER, ncols=3.0, nrows=3.0)
            write_ascii(path, header, data)
            actual_header, actual = generate.read_ascii(path)
            self.assertEqual(actual_header["ncols"], 3)
            np.testing.assert_allclose(actual, data)

    def test_snap_uses_all_four_corners_and_returns_ordered_effective_bounds(self) -> None:
        calls = []

        def transform(points, source, target):
            calls.append((list(points), source, target))
            if len(calls) == 1:
                return [
                    (500060.0, 3500060.0),  # SW
                    (500030.0, 3500150.0),  # NW
                    (500200.0, 3500030.0),  # SE
                    (500180.0, 3500200.0),  # NE
                ]
            return [
                (118.02, 31.03),
                (118.01, 31.18),
                (118.17, 31.02),
                (118.18, 31.20),
            ]

        with unittest.mock.patch("lisflood_runner.generate.transform_points", side_effect=transform):
            window, effective = snap_bounds([[31.0, 118.0], [31.1, 118.1]], self.HEADER, 300)

        self.assertEqual(
            calls[0],
            (
                [(118.0, 31.0), (118.0, 31.1), (118.1, 31.0), (118.1, 31.1)],
                "EPSG:4326",
                "EPSG:32650",
            ),
        )
        self.assertEqual(
            calls[1],
            (
                [
                    (500030.0, 3500030.0),
                    (500030.0, 3500180.0),
                    (500180.0, 3500030.0),
                    (500180.0, 3500180.0),
                ],
                "EPSG:32650",
                "EPSG:4326",
            ),
        )
        self.assertEqual(window, (1, 1, 6, 6))
        self.assertEqual(effective, [[31.02, 118.01], [31.20, 118.18]])

    def test_one_cell_ascii_round_trip_stays_two_dimensional(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "one.asc"
            write_ascii(path, self.HEADER, np.array([[7.5]]))
            _, actual = generate.read_ascii(path)
            self.assertEqual(actual.shape, (1, 1))
            self.assertEqual(actual[0, 0], 7.5)

    def test_snap_rejects_malformed_inputs_before_transform(self) -> None:
        malformed_bounds = (
            None,
            [],
            [[31.0, 118.0]],
            [[31.0, 118.0], [31.1]],
            [[31.0, 118.0], ["bad", 118.1]],
            [[31.0, 118.0], [float("nan"), 118.1]],
        )
        for bounds in malformed_bounds:
            with self.subTest(bounds=bounds), unittest.mock.patch(
                "lisflood_runner.generate.transform_points"
            ) as transform:
                with self.assertRaises(ValueError):
                    snap_bounds(bounds, self.HEADER, 300)
                transform.assert_not_called()

        for max_area in (True, False, float("nan"), float("inf"), 0, -1, "300", 10**400):
            with self.subTest(max_area=max_area), unittest.mock.patch(
                "lisflood_runner.generate.transform_points"
            ) as transform:
                with self.assertRaises(ValueError):
                    snap_bounds([[31.0, 118.0], [31.1, 118.1]], self.HEADER, max_area)
                transform.assert_not_called()

        for header in (
            dict(self.HEADER, ncols=4.5),
            dict(self.HEADER, nrows=float("nan")),
            dict(self.HEADER, xllcorner=float("inf")),
            dict(self.HEADER, cellsize=0),
        ):
            with self.subTest(header=header), unittest.mock.patch(
                "lisflood_runner.generate.transform_points"
            ) as transform:
                with self.assertRaises(ValueError):
                    snap_bounds([[31.0, 118.0], [31.1, 118.1]], header, 300)
                transform.assert_not_called()


class ValidationTests(unittest.TestCase):
    HEADER = {
        "ncols": 4.0, "nrows": 4.0, "xllcorner": 500000.0,
        "yllcorner": 3500000.0, "cellsize": 30.0, "nodata_value": -9999.0,
    }

    def reject(self, *, header=None, dem=None, population=None, window=(0, 0, 2, 2), period=20, bounds=None):
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            with self.assertRaises(ValueError):
                run_job(
                    Path("missing-engine"),
                    header or self.HEADER,
                    dem if dem is not None else np.ones((4, 4)),
                    population if population is not None else np.ones((4, 4)),
                    window,
                    period,
                    bounds or [[32.0, 118.0], [32.1, 118.1]],
                    "data",
                    staging,
                )
            self.assertFalse(staging.exists())

    def test_rejects_non_integer_or_out_of_range_windows(self) -> None:
        for window in ((0.0, 0, 2, 2), (False, 0, 2, 2), (-1, 0, 2, 2), (0, 0, 4, 5), (2, 2, 2, 3)):
            with self.subTest(window=window):
                self.reject(window=window)

    def test_rejects_non_integer_or_unsupported_periods(self) -> None:
        for period in (20.0, True, 7):
            with self.subTest(period=period):
                self.reject(period=period)

    def test_rejects_invalid_base_grids_and_cellsize(self) -> None:
        for header, dem, population in (
            (dict(self.HEADER, nrows=3.0), np.ones((4, 4)), np.ones((4, 4))),
            (self.HEADER, np.ones(16), np.ones((4, 4))),
            (self.HEADER, np.ones((4, 4)), np.ones((3, 4))),
            (dict(self.HEADER, cellsize=0.0), np.ones((4, 4)), np.ones((4, 4))),
        ):
            with self.subTest(header=header, dem_shape=dem.shape, population_shape=population.shape):
                self.reject(header=header, dem=dem, population=population)

    def test_rejects_nonfinite_or_unordered_effective_bounds(self) -> None:
        for bounds in (
            [[32.0, 118.0], [float("nan"), 118.1]],
            [[32.1, 118.0], [32.0, 118.1]],
            [[32.0, 118.1], [32.1, 118.0]],
            [[32.0, 118.0, 0.0], [32.1, 118.1, 0.0]],
        ):
            with self.subTest(bounds=bounds):
                self.reject(bounds=bounds)


class RainfallTests(unittest.TestCase):
    def test_design_storm_matches_nanjing_total_and_peak(self) -> None:
        totals = []
        for period in RETURN_PERIODS:
            rates = design_storm(period)
            expected = (
                16.696 * (1 + 0.954 * np.log10(period))
                / (180 + 18.825) ** 0.751
                * 180
            )
            self.assertEqual(len(rates), 180)
            self.assertTrue(np.all(rates >= 0))
            self.assertAlmostEqual(float(rates.sum() / 60), expected, places=8)
            self.assertIn(int(np.argmax(rates)), (70, 71))
            totals.append(expected)
        self.assertEqual(totals, sorted(totals))

    def test_rain_file_matches_the_official_engine_and_conserves_total(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "design.rain"
            rates = design_storm(5)
            write_rainfall(path, rates)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertTrue(lines[0].startswith("#"))
            self.assertEqual(lines[1], "181\tseconds")
            self.assertEqual(len(lines), 183)
            file_rates = np.array([float(line.split()[0]) for line in lines[2:]])
            self.assertEqual([int(line.split()[1]) for line in lines[2:]], list(range(0, 10801, 60)))
            integrated = np.sum((file_rates[:-1] + file_rates[1:]) * 0.5) / 60
            self.assertAlmostEqual(float(integrated), float(rates.sum() / 60), places=7)


class RiskTests(unittest.TestCase):
    def test_official_hazard_output_name_is_used(self) -> None:
        self.assertEqual(HAZARD_SUFFIX, ".maxHaz")

    def test_risk_matrix_and_dry_cells(self) -> None:
        depth = np.full((4, 4), 0.5)
        hazard = np.repeat([[0.5], [1.0], [2.0], [3.0]], 4, axis=1)
        population = np.repeat([[1, 2, 3, 4]], 4, axis=0)

        risk, breaks = build_risk(depth, hazard, population)

        np.testing.assert_array_equal(
            risk,
            [[1, 1, 1, 2], [1, 2, 2, 3], [2, 2, 3, 4], [2, 3, 4, 4]],
        )
        np.testing.assert_allclose(breaks, [1.75, 2.5, 3.25])

        depth[0, 0] = 0.09
        population[0, 1] = 0
        risk, _ = build_risk(depth, hazard, population)
        self.assertEqual(risk[0, 0], 0)
        self.assertEqual(risk[0, 1], 1)

    def test_all_zero_population_uses_zero_exposure_class(self) -> None:
        depth = np.full((2, 2), 0.5)
        hazard = np.full((2, 2), 1.0)
        population = np.zeros((2, 2))

        risk, breaks = build_risk(depth, hazard, population)

        self.assertEqual(breaks, [0.0, 0.0, 0.0])
        np.testing.assert_array_equal(risk, np.ones((2, 2), dtype=np.uint8))

    def test_nonfinite_population_is_zero_exposure_even_with_positive_values(self) -> None:
        depth = np.full((2, 3), 0.5)
        hazard = np.full((2, 3), 0.5)
        population = np.array([[1.0, np.nan, np.inf], [-np.inf, 3.0, 5.0]])

        risk, breaks = build_risk(depth, hazard, population)

        self.assertEqual(breaks, [2.0, 3.0, 4.0])
        np.testing.assert_array_equal(risk, [[1, 1, 1], [1, 1, 2]])


class ModelConfigurationTests(unittest.TestCase):
    def test_surface_configuration_is_code_owned_and_has_no_drainage_inputs(self) -> None:
        parameters = generate.PARAMETERS
        active = [
            line.split()[0].lower()
            for line in parameters.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        self.assertEqual(
            active,
            [
                "demfile", "resroot", "dirroot", "sim_time", "initial_tstep",
                "massint", "saveint", "acceleration", "fpfric", "infiltration",
                "hazard", "depththresh", "comp_out", "rainfall", "evaporation",
            ],
        )
        self.assertNotRegex(
            parameters,
            r"(?im)^\s*(inpfile|uniform_rules|fv1|dg2|bcifile|startfile|manningfile)\b",
        )
        self.assertIn("DEMfile dem.asc", parameters)
        self.assertIn("resroot result", parameters)
        self.assertIn("rainfall design.rain", parameters)
        self.assertIn("evaporation evaporation.evap", parameters)

    def test_model_version_identifies_official_acc_solver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = Path(directory) / "lisflood"
            engine.write_text("#!/bin/sh\necho 'LISFLOOD-FP version 8.0.3 (double)'\n", encoding="utf-8")
            engine.chmod(0o755)
            self.assertEqual(model_version(engine), "8.0.3 ACC")

    def test_model_version_rejects_arbitrary_usage_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = Path(directory) / "lisflood"
            engine.write_text("#!/bin/sh\necho 'usage: lisflood [options]'\n", encoding="utf-8")
            engine.chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, "LISFLOOD-FP"):
                model_version(engine)

    def test_transform_points_uses_a_short_subprocess_timeout(self) -> None:
        result = subprocess.CompletedProcess(
            ["gdaltransform"], 0, stdout="1 2\n", stderr=""
        )
        with unittest.mock.patch(
            "lisflood_runner.generate.subprocess.run", return_value=result
        ) as runner:
            self.assertEqual(
                generate.transform_points([(0, 0)], "EPSG:4326", "EPSG:32650"),
                [(1.0, 2.0)],
            )
        runner.assert_called_once_with(
            ["gdaltransform", "-s_srs", "EPSG:4326", "-t_srs", "EPSG:32650"],
            input="0 0\n",
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )


class RunnerTests(unittest.TestCase):
    HEADER = {
        "ncols": 4.0, "nrows": 4.0, "xllcorner": 500000.0,
        "yllcorner": 3500000.0, "cellsize": 30.0, "nodata_value": -9999.0,
    }

    @staticmethod
    def _engine(
        directory: str,
        fail: bool = False,
        all_nodata: bool = False,
        nonfinite_hazard: bool = False,
        nonfinite_depth: bool = False,
    ) -> Path:
        engine = Path(directory) / "fake-lisflood"
        if fail:
            script = "#!/bin/sh\nexit 1\n"
        else:
            depth_rows = (
                "-9999.000000 -9999.000000\n-9999.000000 -9999.000000"
                if all_nodata
                else "inf 0.050000\n0.100000 0.500000"
                if nonfinite_depth
                else "0.200000 0.050000\n0.100000 0.500000"
            )
            hazard_rows = (
                "-9999.000000 1.300000\n2.700000 3.000000"
                if nonfinite_hazard
                else "0.500000 1.300000\n2.700000 3.000000"
            )
            script = """#!/bin/sh
set -eu
if [ "$1" = "-version" ] || [ "$1" = "-v" ]; then
  echo 'LISFLOOD-FP version 8.0.3 (double)'
  exit 0
fi
test "$(sed -n '1p' dem.asc)" = "ncols 2"
test "$(sed -n '2p' dem.asc)" = "nrows 2"
test "$(sed -n '3p' dem.asc)" = "xllcorner 500030.000000"
test "$(sed -n '4p' dem.asc)" = "yllcorner 3500030.000000"
test "$(sed -n '5p' dem.asc)" = "cellsize 30.000000"
grep -Fxq 'DEMfile dem.asc' web.par
grep -Fxq 'resroot result' web.par
grep -Fxq 'dirroot results' web.par
grep -Fxq 'sim_time 43200' web.par
grep -Fxq 'initial_tstep 10' web.par
grep -Fxq 'massint 3600' web.par
grep -Fxq 'saveint 3600' web.par
grep -Fxq 'acceleration' web.par
grep -Fxq 'fpfric 0.06' web.par
grep -Fxq 'infiltration 0.00001' web.par
grep -Fxq 'hazard' web.par
grep -Fxq 'depththresh 0.01' web.par
grep -Fxq 'comp_out' web.par
grep -Fxq 'rainfall design.rain' web.par
grep -Fxq 'evaporation evaporation.evap' web.par
! grep -Eiq '^[[:space:]]*(inpFile|uniform_rules|fv1|dg2|bcifile|startfile|manningfile)([[:space:]]|$)' web.par
test -f design.rain
test -f evaporation.evap
mkdir -p results
cat > results/result.mass <<'EOF'
Time Tstep MinTstep NumTsteps Area Vol Qin Hds Qout Qerror Verror Rain-(Inf+Evap)
600 1 1 10 20 100 0 0 0 0 0 100
EOF
cat > results/result.max <<'EOF'
ncols 2
nrows 2
xllcorner 500030.000000
yllcorner 3500030.000000
cellsize 30.000000
NODATA_value -9999
DEPTH_ROWS
EOF
cat > results/result.maxHaz <<'EOF'
ncols 2
nrows 2
xllcorner 500030.000000
yllcorner 3500030.000000
cellsize 30.000000
NODATA_value -9999
HAZARD_ROWS
EOF
"""
            script = script.replace("DEPTH_ROWS", depth_rows).replace("HAZARD_ROWS", hazard_rows)
        engine.write_text(script, encoding="utf-8")
        engine.chmod(0o755)
        return engine

    def test_all_nodata_cropped_dem_is_rejected_before_engine_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "invoked"
            engine = root / "fake-lisflood"
            engine.write_text(f"#!/bin/sh\nset -eu\ntouch '{marker}'\n", encoding="utf-8")
            engine.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "cropped DEM"):
                run_job(
                    engine,
                    self.HEADER,
                    np.full((4, 4), np.nan),
                    np.ones((4, 4)),
                    (1, 1, 3, 3),
                    20,
                    [[32.0, 118.0], [32.1, 118.1]],
                    "data",
                    root / "staging",
                )
            self.assertFalse(marker.exists())

    def test_run_job_writes_flat_manifest_and_layers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = self._engine(directory)
            dem = np.arange(16, dtype=float).reshape(4, 4)
            population = np.array(
                [[0, 1, 2, 3], [4, np.nan, 6, 7], [8, 9, 10, 11], [12, 13, 14, 15]],
                dtype=float,
            )
            staging = root / "staging"

            manifest = run_job(
                engine,
                self.HEADER,
                dem,
                population,
                (np.int64(1), np.int64(1), np.int64(3), np.int64(3)),
                np.int64(20),
                [[np.float64(32.0), np.float64(118.0)], [np.float64(32.1), np.float64(118.1)]],
                "sha256:data",
                staging,
            )

            self.assertEqual(manifest, json.loads((staging / "manifest.json").read_text()))
            self.assertEqual(manifest["schemaVersion"], 1)
            self.assertEqual(manifest["dataVersion"], "sha256:data")
            self.assertEqual(manifest["returnPeriod"], 20)
            self.assertEqual(manifest["bounds"], [[32.0, 118.0], [32.1, 118.1]])
            self.assertIs(type(manifest["returnPeriod"]), int)
            self.assertTrue(all(type(value) is float for corner in manifest["bounds"] for value in corner))
            json.dumps(manifest, allow_nan=False)
            self.assertEqual(
                manifest["layers"],
                {name: f"{name}.png" for name in ("dem", "population", "depth", "hazard", "risk")},
            )
            self.assertEqual(manifest["stats"]["floodedAreaKm2"], 0.003)
            self.assertEqual(manifest["stats"]["exposedPopulation"], 19)
            self.assertEqual(manifest["stats"]["maximumDepthM"], 0.5)
            for name in ("dem", "population", "depth", "hazard", "risk"):
                self.assertTrue((staging / f"{name}.png").is_file())

    def test_relative_engine_path_is_resolved_before_temp_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = self._engine(directory)
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                manifest = run_job(
                    Path(engine.name),
                    self.HEADER,
                    np.ones((4, 4)),
                    np.ones((4, 4)),
                    (1, 1, 3, 3),
                    20,
                    [[32.0, 118.0], [32.1, 118.1]],
                    "data",
                    root / "staging",
                )
            finally:
                os.chdir(old_cwd)
            self.assertEqual(manifest["modelVersion"], "8.0.3 ACC")

    def test_nonfinite_population_is_zeroed_before_risk_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = run_job(
                self._engine(directory),
                self.HEADER,
                np.ones((4, 4)),
                np.array(
                    [[0, 0, 0, 0], [np.nan, np.inf, 0, 0], [-np.inf, np.nan, 0, 0], [0, 0, 0, 0]],
                    dtype=float,
                ),
                (1, 1, 3, 3),
                20,
                [[32.0, 118.0], [32.1, 118.1]],
                "data",
                root / "staging",
            )
            self.assertEqual(manifest["populationBreaks"], [0.0, 0.0, 0.0])
            self.assertEqual(manifest["stats"]["exposedPopulation"], 0)

    def test_nonfinite_hazard_is_not_counted_as_flooded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            population = np.array(
                [[0, 0, 0, 0], [0, 1, 2, 0], [0, 3, 4, 0], [0, 0, 0, 0]],
                dtype=float,
            )
            manifest = run_job(
                self._engine(directory, nonfinite_hazard=True),
                self.HEADER,
                np.ones((4, 4)),
                population,
                (1, 1, 3, 3),
                20,
                [[32.0, 118.0], [32.1, 118.1]],
                "data",
                root / "staging",
            )
            self.assertEqual(manifest["stats"]["floodedAreaKm2"], 0.002)
            self.assertEqual(manifest["stats"]["exposedPopulation"], 7)

    def test_all_nodata_depth_is_rejected_without_nan_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "finite depth"):
                run_job(
                    self._engine(directory, all_nodata=True),
                    self.HEADER,
                    np.ones((4, 4)),
                    np.ones((4, 4)),
                    (1, 1, 3, 3),
                    20,
                    [[32.0, 118.0], [32.1, 118.1]],
                    "data",
                    root / "staging",
                )

    def test_nonfinite_maximum_depth_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "finite depth"):
                run_job(
                    self._engine(directory, nonfinite_depth=True),
                    self.HEADER,
                    np.ones((4, 4)),
                    np.ones((4, 4)),
                    (1, 1, 3, 3),
                    20,
                    [[32.0, 118.0], [32.1, 118.1]],
                    "data",
                    root / "staging",
                )

    def test_failed_engine_does_not_write_manifest_or_layers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            with self.assertRaises(subprocess.CalledProcessError):
                run_job(
                    self._engine(directory, fail=True),
                    self.HEADER,
                    np.ones((4, 4)),
                    np.ones((4, 4)),
                    (1, 1, 3, 3),
                    20,
                    [[32.0, 118.0], [32.1, 118.1]],
                    "data",
                    staging,
                )
            self.assertFalse((staging / "manifest.json").exists())
            self.assertEqual(list(staging.iterdir()), [])

    def test_mass_balance_uses_cumulative_volume_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.mass"
            path.write_text(
                "Time Tstep MinTstep NumTsteps Area Vol Qin Hds Qout Qerror Verror Rain-(Inf+Evap)\n"
                "600 1 1 10 20 100 0 0 0 0 1 50\n"
                "1200 1 1 20 30 200 0 0 0 0 2 150\n",
                encoding="utf-8",
            )
            self.assertAlmostEqual(mass_balance_error(path), 0.015)

    def test_mass_balance_rejects_legacy_rain_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.mass"
            path.write_text(
                "Time Tstep MinTstep NumTsteps Area Vol Qin Hds Qout Qerror Verror Rain-Inf+Evap\n"
                "600 1 1 10 20 100 0 0 0 0 1 50\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unrecognised mass-balance"):
                mass_balance_error(path)


class DockerfileTests(unittest.TestCase):
    def test_t025_mass_file_is_checked_by_the_runner_parser_before_cleanup(self) -> None:
        dockerfile = (Path(__file__).parent / "Dockerfile").read_text(encoding="utf-8")
        smoke_mass = "cp results_rain/res_rain.mass /tmp/lisflood-t025.mass"
        parser_import = "from lisflood_runner.generate import mass_balance_error"
        self.assertIn("curl -fL --retry 3 --retry-all-errors", dockerfile)
        self.assertIn(smoke_mass, dockerfile)
        self.assertLess(dockerfile.index(smoke_mass), dockerfile.index("rm -rf /tmp/build /tmp/source /tmp/lisflood.zip"))
        self.assertIn(parser_import, dockerfile)
        self.assertIn("mass_balance_error(mass)", dockerfile)
        self.assertIn("mass.unlink()", dockerfile)
        self.assertLess(dockerfile.index("COPY lisflood_runner /app/lisflood_runner"), dockerfile.index(parser_import))


if __name__ == "__main__":
    unittest.main()
