"""Regression coverage for selectable variables and prediction models."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

try:
    import numpy as np
    import pandas as pd
    from wasp.prediction import run_wasp_prediction
    from wasp.utils import make_demo_csv
except ImportError:
    np = None
    pd = None


@unittest.skipUnless(
    pd is not None and np is not None,
    "scientific backend dependencies not installed",
)
class ModelExpansionTests(unittest.TestCase):
    def model_module(self):
        try:
            return importlib.import_module("wasp.models")
        except ModuleNotFoundError as error:
            self.fail(f"selectable model module is missing: {error}")

    def test_model_allowlist_and_factories_use_safe_fixed_parameters(self) -> None:
        models = self.model_module()

        self.assertEqual(models.SUPPORTED_MODELS, frozenset({"linear", "knn", "xgboost"}))
        self.assertIsNone(models.validate_model("linear"))
        self.assertIn("Unsupported model", models.validate_model("ridge"))

        linear = models.build_regressor("linear")
        knn = models.build_regressor("knn")
        xgboost = models.build_regressor("xgboost")

        self.assertEqual(type(linear).__name__, "LinearRegression")
        self.assertEqual(knn.get_params()["n_neighbors"], 5)
        self.assertEqual(knn.get_params()["weights"], "distance")
        self.assertEqual(knn.get_params()["p"], 2)
        self.assertEqual(knn.get_params()["n_jobs"], 1)
        self.assertEqual(xgboost.get_params()["n_estimators"], 100)
        self.assertEqual(xgboost.get_params()["max_depth"], 3)
        self.assertEqual(xgboost.get_params()["learning_rate"], 0.05)
        self.assertEqual(xgboost.get_params()["subsample"], 0.8)
        self.assertEqual(xgboost.get_params()["colsample_bytree"], 0.8)
        self.assertEqual(xgboost.get_params()["tree_method"], "hist")
        self.assertEqual(xgboost.get_params()["n_jobs"], 1)
        self.assertEqual(xgboost.get_params()["random_state"], 42)

    def test_all_models_run_for_wasp_and_raw_baseline(self) -> None:
        expected_kinds = {
            "linear": "coefficient",
            "knn": "none",
            "xgboost": "importance",
        }

        for model, attribution_kind in expected_kinds.items():
            with self.subTest(model=model):
                result = run_wasp_prediction(
                    contents=make_demo_csv(),
                    filename="demo.csv",
                    wavelet="db4",
                    level=None,
                    test_size=0.2,
                    model=model,
                )

                self.assertTrue(result["success"], result.get("message"))
                self.assertEqual(result["model"], model)
                self.assertIn("model_label", result)
                self.assertIn("wasp", result["metrics"])
                self.assertIn("baseline", result["metrics"])
                self.assertEqual(result["feature_attributions"]["kind"], attribution_kind)
                if model == "linear":
                    self.assertTrue(result["model_coefficients"])
                else:
                    self.assertEqual(result["model_coefficients"], [])
                json.dumps(result, allow_nan=False)

    def test_default_variable_selection_uses_first_and_remaining_columns(self) -> None:
        result = run_wasp_prediction(
            contents=make_demo_csv(),
            filename="demo.csv",
            model="linear",
        )

        self.assertTrue(result["success"], result.get("message"))
        self.assertEqual(result["target_column"], "streamflow_anomaly")
        self.assertEqual(
            result["predictor_columns"],
            ["sst_index", "soi", "pdo_index", "precip_index"],
        )

    def test_xgboost_prediction_is_deterministic(self) -> None:
        arguments = {
            "contents": make_demo_csv(),
            "filename": "demo.csv",
            "wavelet": "db4",
            "level": None,
            "test_size": 0.2,
            "model": "xgboost",
        }

        first = run_wasp_prediction(**arguments)
        second = run_wasp_prediction(**arguments)

        self.assertTrue(first["success"], first.get("message"))
        self.assertEqual(first["predictions"], second["predictions"])
        self.assertEqual(first["feature_attributions"], second["feature_attributions"])

    def test_selected_variables_drive_the_prediction(self) -> None:
        values = np.arange(96, dtype=float)
        frame = pd.DataFrame({
            "date": [f"row-{index}" for index in range(96)],
            "unused_text": ["metadata"] * 96,
            "flow": np.sin(values / 8) + values * 0.01,
            "rain": np.cos(values / 11) + values * 0.02,
            "temperature": np.sin(values / 17) - values * 0.005,
        })
        contents = frame.to_csv(index=False).encode()

        result = run_wasp_prediction(
            contents=contents,
            filename="selection.csv",
            wavelet="db2",
            level=None,
            test_size=0.25,
            model="linear",
            target_column="flow",
            predictor_columns=["temperature", "rain"],
        )

        self.assertTrue(result["success"], result.get("message"))
        self.assertEqual(result["target_column"], "flow")
        self.assertEqual(result["predictor_columns"], ["temperature", "rain"])

    def test_db16_supports_half_split_or_returns_a_clear_short_series_error(self) -> None:
        valid = run_wasp_prediction(
            contents=make_demo_csv(),
            filename="demo.csv",
            wavelet="db16",
            level=None,
            test_size=0.5,
            model="linear",
        )
        self.assertTrue(valid["success"], valid.get("message"))
        self.assertGreaterEqual(valid["level"], 1)

        values = np.arange(30, dtype=float)
        short = pd.DataFrame({"target": values, "predictor": values * 2 + 1})
        invalid = run_wasp_prediction(
            contents=short.to_csv(index=False).encode(),
            filename="short.csv",
            wavelet="db16",
            level=None,
            test_size=0.5,
            model="linear",
        )
        self.assertFalse(invalid["success"])
        self.assertIn("at least one wavelet decomposition level", invalid["message"])

    def test_xgboost_dependency_is_pinned_for_the_python_311_image(self) -> None:
        requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("xgboost==3.1.2", requirements)


if __name__ == "__main__":
    unittest.main()
