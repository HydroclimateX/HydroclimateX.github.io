"""Behaviour checks for resource-safe WASP CSV and wavelet validation."""

from __future__ import annotations

from io import BytesIO
import asyncio
import inspect
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

try:
    import numpy as np
    import pandas as pd
    from wasp.utils import (
        MAX_PREDICTORS,
        MAX_ROWS,
        MAX_ABS_VALUE,
        SUPPORTED_WAVELETS,
        compute_metrics,
        make_json_safe,
        make_demo_csv,
        validate_data,
        validate_wavelet,
    )
    from wasp.prediction import run_wasp_prediction
except ImportError:
    np = None
    pd = None

try:
    from app import predict as api_predict
    from fastapi import BackgroundTasks
    from starlette.datastructures import UploadFile
    from starlette.responses import JSONResponse
except (ImportError, RuntimeError):
    api_predict = None


@unittest.skipUnless(
    pd is not None and np is not None,
    "scientific backend dependencies not installed",
)
class InputValidationTests(unittest.TestCase):
    @staticmethod
    def csv_bytes(frame: "pd.DataFrame") -> bytes:
        buffer = BytesIO()
        frame.to_csv(buffer, index=False)
        return buffer.getvalue()

    def valid_frame(self, rows: int = 30) -> "pd.DataFrame":
        values = np.arange(rows, dtype=float)
        return pd.DataFrame({"target": values, "predictor": values * 2 + 1})

    def assert_invalid(self, frame: "pd.DataFrame", message: str) -> None:
        parsed, error = validate_data(self.csv_bytes(frame), "input.csv")
        self.assertTrue(parsed.empty)
        self.assertIsNotNone(error)
        self.assertIn(message, error)

    def test_valid_numeric_finite_csv_is_accepted(self) -> None:
        parsed, error = validate_data(self.csv_bytes(self.valid_frame()), "input.csv")
        self.assertIsNone(error)
        self.assertEqual(parsed.shape, (30, 2))

    def test_explicit_variable_selection_ignores_unselected_text_columns(self) -> None:
        values = np.arange(30, dtype=float)
        frame = pd.DataFrame({
            "date": [f"2026-01-{index + 1:02d}" for index in range(30)],
            "flow": values,
            "rain": values * 0.5 + 1,
            "temperature": values * -0.25,
        })

        parsed, error = validate_data(
            self.csv_bytes(frame),
            "input.csv",
            target_column="flow",
            predictor_columns=["temperature", "rain"],
        )

        self.assertIsNone(error)
        self.assertEqual(parsed.columns.tolist(), ["flow", "temperature", "rain"])
        self.assertEqual(parsed.shape, (30, 3))

    def test_variable_selection_rejects_invalid_column_combinations(self) -> None:
        contents = self.csv_bytes(self.valid_frame())
        cases = [
            ("missing", ["predictor"], "Target column 'missing'"),
            ("target", ["missing"], "Predictor column 'missing'"),
            ("target", ["predictor", "predictor"], "must be unique"),
            ("target", ["target"], "cannot also be a predictor"),
            ("target", [], "at least one predictor"),
        ]

        for target, predictors, message in cases:
            with self.subTest(target=target, predictors=predictors):
                parsed, error = validate_data(
                    contents,
                    "input.csv",
                    target_column=target,
                    predictor_columns=predictors,
                )
                self.assertTrue(parsed.empty)
                self.assertIn(message, error)

    def test_rejects_blank_and_duplicate_csv_headers(self) -> None:
        rows = "\n".join(f"{index},{index + 1}" for index in range(30))
        for header, message in (("target,", "blank"), ("target,target", "unique")):
            with self.subTest(header=header):
                parsed, error = validate_data(
                    f"{header}\n{rows}\n".encode(),
                    "input.csv",
                )
                self.assertTrue(parsed.empty)
                self.assertIn(message, error)

    def test_requires_at_least_one_predictor(self) -> None:
        self.assert_invalid(self.valid_frame().iloc[:, :1], "at least 2 columns")

    def test_rejects_non_numeric_and_non_finite_values(self) -> None:
        non_numeric = self.valid_frame().astype(object)
        non_numeric.loc[3, "predictor"] = "not-a-number"
        self.assert_invalid(non_numeric, "numeric")
        non_finite = self.valid_frame()
        non_finite.loc[3, "predictor"] = np.inf
        self.assert_invalid(non_finite, "finite")

    def test_rejects_constant_target_and_predictors(self) -> None:
        constant_target = self.valid_frame()
        constant_target["target"] = 1.0
        self.assert_invalid(constant_target, "Target")
        constant_predictor = self.valid_frame()
        constant_predictor["predictor"] = 1.0
        self.assert_invalid(constant_predictor, "predictor")

    def test_enforces_resource_limits(self) -> None:
        self.assert_invalid(self.valid_frame(MAX_ROWS + 1), f"at most {MAX_ROWS} rows")
        data = {"target": np.arange(30, dtype=float)}
        for index in range(MAX_PREDICTORS + 1):
            data[f"p{index}"] = np.arange(30, dtype=float) + index
        self.assert_invalid(pd.DataFrame(data), f"at most {MAX_PREDICTORS} predictors")

    def test_preflight_rejects_huge_header_without_calling_pandas(self) -> None:
        header = "," * 19_999
        contents = (header + "\n" + "1," * 19_999 + "1\n").encode()
        self.assertLess(len(contents), 70_000)

        with mock.patch("wasp.utils.pd.read_csv") as read_csv:
            parsed, error = validate_data(contents, "wide.csv")

        self.assertTrue(parsed.empty)
        self.assertIn("at most 51 columns", error)
        read_csv.assert_not_called()

    def test_preflight_rejects_too_many_rows_without_calling_pandas(self) -> None:
        contents = ("target,predictor\n" + "1,2\n" * (MAX_ROWS + 1)).encode()

        with mock.patch("wasp.utils.pd.read_csv") as read_csv:
            parsed, error = validate_data(contents, "long.csv")

        self.assertTrue(parsed.empty)
        self.assertIn(f"at most {MAX_ROWS} rows", error)
        read_csv.assert_not_called()

    def test_preflight_rejects_ragged_rows_without_calling_pandas(self) -> None:
        contents = b"target,predictor\n1,2\n3,4,5\n"

        with mock.patch("wasp.utils.pd.read_csv") as read_csv:
            parsed, error = validate_data(contents, "ragged.csv")

        self.assertTrue(parsed.empty)
        self.assertIn("same number of columns", error)
        read_csv.assert_not_called()

    def test_rejects_values_that_can_overflow_squared_metrics(self) -> None:
        frame = self.valid_frame()
        frame.loc[3, "predictor"] = 1e154
        self.assert_invalid(frame, f"absolute value must not exceed {MAX_ABS_VALUE:g}")

    def test_wavelet_allowlist_matches_the_ui(self) -> None:
        self.assertEqual(
            SUPPORTED_WAVELETS,
            frozenset({"db1", "db2", "db4", "db8", "db16"}),
        )
        for wavelet in ("db1", "db2", "db4", "db8", "db16"):
            self.assertIsNone(validate_wavelet(wavelet))
        self.assertIn("Unsupported wavelet", validate_wavelet("haar"))

    def test_constant_series_metrics_use_none_and_are_json_safe(self) -> None:
        constant_observed = compute_metrics(np.ones(30), np.ones(30))
        self.assertIsNone(constant_observed["nse"])
        self.assertIsNone(constant_observed["kge"])
        self.assertIsNone(constant_observed["correlation"])
        json.dumps(constant_observed, allow_nan=False)

        constant_prediction = compute_metrics(np.arange(30), np.ones(30))
        self.assertIsNone(constant_prediction["kge"])
        self.assertIsNone(constant_prediction["correlation"])
        json.dumps(constant_prediction, allow_nan=False)

    def test_demo_prediction_is_complete_and_strictly_json_serializable(self) -> None:
        result = run_wasp_prediction(
            contents=make_demo_csv(),
            filename="demo.csv",
            wavelet="db4",
            level=None,
            test_size=0.2,
            alpha=1.0,
        )

        self.assertTrue(result["success"], result.get("message"))
        self.assertIn("wasp", result["metrics"])
        self.assertIn("baseline", result["metrics"])
        self.assertTrue(result["predictions"]["observed"])
        self.assertEqual(
            len(result["predictions"]["observed"]),
            len(result["predictions"]["wasp_predicted"]),
        )
        json.dumps(result, allow_nan=False)

    def test_recursive_response_cleaner_covers_all_prediction_sections(self) -> None:
        unsafe = {
            "band_energies": {"predictor": [np.float64(np.nan), np.float64(1.0)]},
            "modulation_factors": {"predictor": (np.inf, -np.inf)},
            "predictions": {"wasp_predicted": np.array([1.0, np.nan])},
        }

        cleaned = make_json_safe(unsafe)

        self.assertEqual(cleaned["band_energies"]["predictor"], [None, 1.0])
        self.assertEqual(cleaned["modulation_factors"]["predictor"], [None, None])
        self.assertEqual(cleaned["predictions"]["wasp_predicted"], [1.0, None])
        json.dumps(cleaned, allow_nan=False)


@unittest.skipUnless(
    api_predict is not None and pd is not None and np is not None,
    "FastAPI and scientific backend dependencies not installed",
)
class ApiInputValidationTests(unittest.TestCase):
    @staticmethod
    def upload(contents: bytes) -> "UploadFile":
        return UploadFile(file=BytesIO(contents), filename="input.csv")

    def call_predict(self, **kwargs) -> object:
        request = mock.Mock()
        request.cookies = {}
        request.headers = {}
        request.client = None
        return asyncio.run(
            api_predict(
                request=request,
                background_tasks=BackgroundTasks(),
                response=mock.Mock(),
                **kwargs,
            )
        )

    def test_api_default_test_size_remains_twenty_percent(self) -> None:
        test_size = inspect.signature(api_predict).parameters["test_size"].default
        self.assertEqual(test_size.default, 0.2)

    def test_api_wavelet_contract_documents_db16(self) -> None:
        wavelet = inspect.signature(api_predict).parameters["wavelet"].default
        self.assertEqual(wavelet.default, "db16")
        self.assertIn("db1", wavelet.description)
        self.assertIn("db2", wavelet.description)
        self.assertIn("db4", wavelet.description)
        self.assertIn("db8", wavelet.description)
        self.assertIn("db16", wavelet.description)

    def test_unsupported_wavelet_is_a_structured_400(self) -> None:
        rows = ["target,predictor"] + [f"{index},{index + 1}" for index in range(30)]
        response = self.call_predict(
            file=self.upload(("\n".join(rows) + "\n").encode()),
            wavelet="sym8",
            level=0,
            test_size=0.2,
            model="linear",
            target_column=None,
            predictor_columns=None,
        )
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body)
        self.assertFalse(payload["success"])
        self.assertIn("Unsupported wavelet", payload["message"])

    def test_api_accepts_selected_variables_and_model(self) -> None:
        rows = ["date,target,predictor,unused"] + [
            f"day-{index},{index},{index * 2 + 1},metadata"
            for index in range(64)
        ]
        response = self.call_predict(
            file=self.upload(("\n".join(rows) + "\n").encode()),
            wavelet="db2",
            level=0,
            test_size=0.5,
            model="knn",
            target_column="target",
            predictor_columns=["predictor"],
        )

        self.assertNotIsInstance(response, JSONResponse)
        self.assertTrue(response["success"])
        self.assertEqual(response["model"], "knn")
        self.assertEqual(response["target_column"], "target")
        self.assertEqual(response["predictor_columns"], ["predictor"])

    def test_unsupported_model_is_a_structured_400(self) -> None:
        rows = ["target,predictor"] + [f"{index},{index + 1}" for index in range(64)]
        response = self.call_predict(
            file=self.upload(("\n".join(rows) + "\n").encode()),
            wavelet="db2",
            level=0,
            test_size=0.2,
            model="ridge",
            target_column="target",
            predictor_columns=["predictor"],
        )

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body)
        self.assertIn("Unsupported model", payload["message"])

    def test_invalid_csv_is_a_structured_400(self) -> None:
        rows = ["target,predictor"] + [f"{index},bad" for index in range(30)]
        response = self.call_predict(
            file=self.upload(("\n".join(rows) + "\n").encode()),
            wavelet="db4",
            level=0,
            test_size=0.2,
            model="linear",
            target_column=None,
            predictor_columns=None,
        )
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body)
        self.assertFalse(payload["success"])
        self.assertIn("numeric", payload["message"])

    def test_extreme_finite_csv_is_a_structured_json_safe_400(self) -> None:
        rows = ["target,predictor"] + [f"{index},{index + 1}" for index in range(30)]
        rows[4] = "3,1e154"
        response = self.call_predict(
            file=self.upload(("\n".join(rows) + "\n").encode()),
            wavelet="db4",
            level=0,
            test_size=0.2,
            model="linear",
            target_column=None,
            predictor_columns=None,
        )
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body)
        self.assertFalse(payload["success"])
        json.dumps(payload, allow_nan=False)

    def test_prediction_runs_in_threadpool_behind_single_task_semaphore(self) -> None:
        rows = ["target,predictor"] + [f"{index},{index + 1}" for index in range(30)]

        with mock.patch(
            "app.run_in_threadpool",
            new=mock.AsyncMock(return_value={"success": True, "metrics": {}, "predictions": {}}),
        ) as runner:
            response = self.call_predict(
                file=self.upload(("\n".join(rows) + "\n").encode()),
                wavelet="db4",
                level=0,
                test_size=0.2,
                model="linear",
                target_column=None,
                predictor_columns=None,
            )

        self.assertTrue(response["success"])
        self.assertIsInstance(response["analytics_run_id"], str)
        runner.assert_awaited_once()
        source = (ROOT / "backend/app.py").read_text(encoding="utf-8")
        self.assertIn("asyncio.Semaphore(1)", source)
        self.assertIn("async with PREDICTION_SEMAPHORE", source)


if __name__ == "__main__":
    unittest.main()
