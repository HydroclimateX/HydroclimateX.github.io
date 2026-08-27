import json
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
            return_value=[(500030.0, 3500030.0), (500180.0, 3500180.0)],
        ):
            window, effective = snap_bounds([[31.0, 118.0], [31.1, 118.1]], self.HEADER, 300)
        self.assertEqual(window, (1, 1, 6, 6))
        self.assertEqual(effective, [[3500030.0, 500030.0], [3500180.0, 500180.0]])
        grid = np.arange(100).reshape(10, 10)
        np.testing.assert_array_equal(crop_grid(grid, window), grid[4:9, 1:6])
        self.assertEqual(job_id(window, 20, "8.0.3", "data"), job_id(window, 20, "8.0.3", "data"))

    def test_rejects_area_above_limit(self) -> None:
        large_header = dict(self.HEADER, ncols=1000.0, nrows=1000.0)
        with unittest.mock.patch(
            "lisflood_runner.generate.transform_points",
            return_value=[(500000.0, 3500000.0), (520000.0, 3520000.0)],
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
            self.assertEqual(lines[0], "181\tseconds")
            file_rates = np.array([float(line.split()[0]) for line in lines[1:]])
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


class RunnerTests(unittest.TestCase):
    HEADER = {
        "ncols": 4.0, "nrows": 4.0, "xllcorner": 500000.0,
        "yllcorner": 3500000.0, "cellsize": 30.0, "nodata_value": -9999.0,
    }

    @staticmethod
    def _engine(directory: str, fail: bool = False) -> Path:
        engine = Path(directory) / "fake-lisflood"
        if fail:
            script = "#!/bin/sh\nexit 1\n"
        else:
            script = """#!/bin/sh
if [ "$1" = "-version" ] || [ "$1" = "-v" ]; then
  echo 'LISFLOOD-FP version 8.0.3 (double)'
  exit 0
fi
mkdir -p results
cat > results/result.mass <<'EOF'
Time Tstep MinTstep NumTsteps Area Vol Qin Hds Qout Qerror Verror Rain-Inf+Evap
600 1 1 10 20 100 0 0 0 0 0 100
EOF
cat > results/result.max <<'EOF'
ncols 2
nrows 2
xllcorner 500030.000000
yllcorner 3500030.000000
cellsize 30.000000
NODATA_value -9999
0.200000 0.050000
0.100000 0.500000
EOF
cat > results/result.maxHaz <<'EOF'
ncols 2
nrows 2
xllcorner 500030.000000
yllcorner 3500030.000000
cellsize 30.000000
NODATA_value -9999
0.500000 1.300000
2.700000 3.000000
EOF
"""
        engine.write_text(script, encoding="utf-8")
        engine.chmod(0o755)
        return engine

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
                (1, 1, 3, 3),
                20,
                [[32.0, 118.0], [32.1, 118.1]],
                "sha256:data",
                staging,
            )

            self.assertEqual(manifest, json.loads((staging / "manifest.json").read_text()))
            self.assertEqual(manifest["schemaVersion"], 1)
            self.assertEqual(manifest["dataVersion"], "sha256:data")
            self.assertEqual(manifest["returnPeriod"], 20)
            self.assertEqual(manifest["bounds"], [[32.0, 118.0], [32.1, 118.1]])
            self.assertEqual(
                manifest["layers"],
                {name: f"{name}.png" for name in ("dem", "population", "depth", "hazard", "risk")},
            )
            self.assertEqual(manifest["stats"]["floodedAreaKm2"], 0.003)
            self.assertEqual(manifest["stats"]["exposedPopulation"], 19)
            self.assertEqual(manifest["stats"]["maximumDepthM"], 0.5)
            for name in ("dem", "population", "depth", "hazard", "risk"):
                self.assertTrue((staging / f"{name}.png").is_file())

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
                "Time Tstep MinTstep NumTsteps Area Vol Qin Hds Qout Qerror Verror Rain-Inf+Evap\n"
                "600 1 1 10 20 100 0 0 0 0 1 50\n"
                "1200 1 1 20 30 200 0 0 0 0 2 150\n",
                encoding="utf-8",
            )
            self.assertAlmostEqual(mass_balance_error(path), 0.015)


if __name__ == "__main__":
    unittest.main()
