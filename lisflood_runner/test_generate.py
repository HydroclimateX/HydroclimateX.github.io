import ast
import inspect
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from lisflood_runner.generate import (
    HAZARD_SUFFIX,
    RETURN_PERIODS,
    build_risk,
    design_storm,
    ensure_cache_space,
    mass_balance_error,
    publish_cache,
    run_all,
    write_rainfall,
)


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

    def test_rain_file_matches_the_modified_engine_and_conserves_total(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "design.rain"
            rates = design_storm(5)
            write_rainfall(path, rates)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "181\tseconds")
            file_rates = np.array([float(line.split()[0]) for line in lines[1:]])
            self.assertAlmostEqual(float(np.trapz(file_rates, dx=1 / 60)), float(rates.sum() / 60), places=7)


class RiskTests(unittest.TestCase):
    def test_modified_v59_hazard_output_name_is_used(self) -> None:
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


class PublishTests(unittest.TestCase):
    def test_runner_publishes_once_after_all_scenarios(self) -> None:
        tree = ast.parse(inspect.getsource(run_all))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "publish_cache"
        ]
        self.assertEqual(len(calls), 1)

    def test_cache_directory_is_created_before_space_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "new-cache"
            ensure_cache_space(cache, minimum_gb=0)
            self.assertTrue(cache.is_dir())

    def test_manifest_is_replaced_only_after_complete_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text('{"old": true}', encoding="utf-8")
            staging = root / ".run.tmp"
            staging.mkdir()
            (staging / "risk.png").write_bytes(b"png")
            manifest = {"schemaVersion": 1, "scenarios": {str(p): {} for p in RETURN_PERIODS}}

            publish_cache(root, staging, "run", manifest)

            self.assertTrue((root / "run" / "risk.png").is_file())
            self.assertEqual(
                json.loads((root / "manifest.json").read_text(encoding="utf-8")),
                manifest,
            )

    def test_incomplete_run_keeps_the_previous_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = '{"old": true}'
            (root / "manifest.json").write_text(old, encoding="utf-8")
            staging = root / ".run.tmp"
            staging.mkdir()
            with self.assertRaises(ValueError):
                publish_cache(root, staging, "run", {"scenarios": {"5": {}}})
            self.assertEqual((root / "manifest.json").read_text(encoding="utf-8"), old)

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
