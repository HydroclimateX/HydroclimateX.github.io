"""Golden and optional live-R parity coverage for the canonical WASP example."""

from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

try:
    from wasp.prediction import run_wasp_prediction
    from wasp.wavelet import covariance_modulation_factors, variance_transform
except ImportError:
    run_wasp_prediction = None


FIXTURE_PATH = ROOT / "tests" / "fixtures" / "wasp_r_parity.json"
DATASET_PATH = ROOT / "examples" / "wasp_demo.csv"
PUBLIC_WAVELETS = ("db1", "db2", "db4", "db8")


def _public_hash(values: list[float]) -> str:
    encoded = ",".join(f"{round(float(value), 4):.4f}" for value in values)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _r_live_script() -> str:
    return r"""
    suppressPackageStartupMessages({library(WASP); library(waveslim); library(jsonlite)})
    d <- read.csv("examples/wasp_demo.csv", check.names=FALSE)
    cal <- d[1:600, , drop=FALSE]
    val <- d[601:1200, , drop=FALSE]
    mapping <- c(db1="haar", db2="d4", db4="d8", db8="d16")
    levels <- c(db1=9L, db2=7L, db4=6L, db8=5L)
    out <- list()
    for (w in names(mapping)) {
      fit <- dwt.vt(list(x=cal[[1]], dp=cal[,-1,drop=FALSE]),
                    wf=mapping[[w]], J=levels[[w]], method="dwt",
                    pad="zero", boundary="periodic", cov.opt="auto",
                    verbose=FALSE)
      valid <- dwt.vt.val(list(x=val[[1]], dp=val[,-1,drop=FALSE]),
                          J=levels[[w]], dwt=fit, verbose=FALSE)
      fac <- apply(fit$S, 2, function(s) as.numeric(s/sqrt(sum(s^2))))
      cal_x <- as.matrix(fit$dp.n)
      val_x <- as.matrix(valid$dp.n)
      cal_y <- cal[[1]]
      val_y <- val[[1]]
      cal_fit <- lm.fit(cbind(1, cal_x), cal_y)
      raw_fit <- lm.fit(cbind(1, as.matrix(cal[,-1,drop=FALSE])), cal_y)
      out[[w]] <- list(
        factors=setNames(lapply(seq_len(ncol(fac)), function(i) as.numeric(fac[,i])), names(cal)[-1]),
        calibration=list(
          transformed=setNames(lapply(seq_len(ncol(cal_x)), function(i) as.numeric(cal_x[,i])), names(cal)[-1]),
          wasp=as.numeric(cbind(1, cal_x) %*% cal_fit$coefficients),
          baseline=as.numeric(cbind(1, as.matrix(cal[,-1,drop=FALSE])) %*% raw_fit$coefficients)
        ),
        validation=list(
          transformed=setNames(lapply(seq_len(ncol(val_x)), function(i) as.numeric(val_x[,i])), names(cal)[-1]),
          wasp=as.numeric(cbind(1, val_x) %*% cal_fit$coefficients),
          baseline=as.numeric(cbind(1, as.matrix(val[,-1,drop=FALSE])) %*% raw_fit$coefficients)
        )
      )
    }
    cat(toJSON(out, auto_unbox=TRUE, digits=17))
    """


@unittest.skipUnless(run_wasp_prediction is not None, "scientific backend dependencies not installed")
class WaspRParityFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.contents = DATASET_PATH.read_bytes()
        cls.results = {
            wavelet: run_wasp_prediction(
                contents=cls.contents,
                filename="wasp_demo.csv",
                wavelet=wavelet,
                level=None,
                test_size=0.5,
                model="linear",
            )
            for wavelet in PUBLIC_WAVELETS
        }
        frame = pd.read_csv(BytesIO(cls.contents))
        y = frame.iloc[:, 0].to_numpy(dtype=float)
        raw = frame.iloc[:, 1:].to_numpy(dtype=float)
        cls.raw_results = {}
        for wavelet in PUBLIC_WAVELETS:
            level = cls.fixture["cases"][wavelet]["level"]
            factors = {}
            cal_transformed = np.empty_like(raw[:600])
            val_transformed = np.empty_like(raw[600:])
            for i, predictor in enumerate(frame.columns[1:]):
                factors[predictor] = np.asarray(
                    covariance_modulation_factors(
                        raw[:600, i], y[:600], wavelet=wavelet, level=level
                    )
                )
                cal_transformed[:, i] = variance_transform(
                    raw[:600, i], factors[predictor], wavelet=wavelet, level=level
                )
                val_transformed[:, i] = variance_transform(
                    raw[600:, i], factors[predictor], wavelet=wavelet, level=level
                )
            cal_design = np.column_stack((np.ones(600), cal_transformed))
            val_design = np.column_stack((np.ones(600), val_transformed))
            raw_cal_design = np.column_stack((np.ones(600), raw[:600]))
            raw_val_design = np.column_stack((np.ones(600), raw[600:]))
            wasp_beta = np.linalg.lstsq(cal_design, y[:600], rcond=None)[0]
            baseline_beta = np.linalg.lstsq(raw_cal_design, y[:600], rcond=None)[0]
            cls.raw_results[wavelet] = {
                "factors": factors,
                "calibration": {
                    "transformed": cal_transformed,
                    "wasp": cal_design @ wasp_beta,
                    "baseline": raw_cal_design @ baseline_beta,
                },
                "validation": {
                    "transformed": val_transformed,
                    "wasp": val_design @ wasp_beta,
                    "baseline": raw_val_design @ baseline_beta,
                },
            }

    def test_canonical_fixture_contains_r_source_metadata(self) -> None:
        fixture = self.fixture
        self.assertEqual(fixture["source"]["dataset"], "examples/wasp_demo.csv")
        self.assertEqual(fixture["source"]["wasp_commit"], "5096903")
        self.assertEqual(fixture["source"]["wasp_version"], "1.4.5")
        self.assertEqual(fixture["protocol"]["test_size"], 0.5)
        self.assertEqual(fixture["protocol"]["output_precision"], 4)
        self.assertEqual(fixture["wavelet_mapping"], {
            "db1": "haar", "db2": "d4", "db4": "d8", "db8": "d16",
        })
        self.assertEqual(fixture["auto_levels"], {"db1": 9, "db2": 7, "db4": 6, "db8": 5})
        self.assertEqual(
            hashlib.sha256(self.contents).hexdigest(),
            fixture["source"]["dataset_sha256"],
        )

    def test_python_public_output_matches_r_golden_fixture(self) -> None:
        for wavelet in PUBLIC_WAVELETS:
            with self.subTest(wavelet=wavelet):
                expected = self.fixture["cases"][wavelet]
                result = self.results[wavelet]
                self.assertTrue(result["success"], result.get("message"))
                self.assertEqual(result["n_train"], 600)
                self.assertEqual(result["n_test"], 600)
                self.assertEqual(result["level"], expected["level"])
                self.assertEqual(result["wavelet"], wavelet)

                for predictor, factors in result["modulation_factors"].items():
                    self.assertEqual(factors, expected["public_modulation_factors"][predictor])
                    self.assertAlmostEqual(
                        sum(float(value) ** 2 for value in expected["modulation_factors"][predictor]),
                        1.0,
                        places=12,
                    )
                    self.assertTrue(all(value == round(value, 4) for value in factors))

                    transformed = result["predictions"]["transformed_predictors"][predictor]
                    for period, values in (
                        ("calibration", transformed[:600]),
                        ("validation", transformed[600:]),
                    ):
                        expected_hash = expected["transformed_predictors"][predictor][period]
                        self.assertEqual(len(values), expected_hash["length"])
                        self.assertEqual(_public_hash(values), expected_hash["sha256"])
                        self.assertTrue(all(value == round(value, 4) for value in values))

                prediction_sets = {
                    "calibration": result["predictions"]["calibration"],
                    "validation": {
                        "observed": result["predictions"]["observed"],
                        "wasp": result["predictions"]["wasp_predicted"],
                        "baseline": result["predictions"]["baseline_predicted"],
                    },
                }
                for period, arrays in prediction_sets.items():
                    for name, values in (
                        ("observed", arrays["observed"]),
                        ("wasp", arrays["wasp_predicted"] if "wasp_predicted" in arrays else arrays["wasp"]),
                        ("baseline", arrays["baseline_predicted"] if "baseline_predicted" in arrays else arrays["baseline"]),
                    ):
                        expected_hash = expected["predictions"][period][name]
                        self.assertEqual(len(values), expected_hash["length"])
                        self.assertEqual(_public_hash(values), expected_hash["sha256"])
                        self.assertTrue(all(value == round(value, 4) for value in values))

                self.assertEqual(result["metrics"], expected["metrics"])

    def test_live_r_full_precision_arrays_match(self) -> None:
        rscript = shutil.which("Rscript")
        if rscript is None:
            self.skipTest("Rscript is not installed")
        availability = subprocess.run(
            [rscript, "-e", "quit(status=ifelse(all(vapply(c('WASP', 'waveslim', 'jsonlite'), requireNamespace, logical(1), quietly=TRUE)), 0, 1))"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if availability.returncode != 0:
            self.skipTest("R WASP and waveslim packages are not installed")
        live = subprocess.run(
            [rscript, "-e", _r_live_script()],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(live.returncode, 0, live.stderr)
        reference = json.loads(live.stdout)
        for wavelet in PUBLIC_WAVELETS:
            with self.subTest(wavelet=wavelet):
                result = self.results[wavelet]
                self.assertTrue(result["success"], result.get("message"))
                expected = reference[wavelet]
                actual = self.raw_results[wavelet]
                for predictor, values in expected["factors"].items():
                    np.testing.assert_allclose(
                        actual["factors"][predictor],
                        values,
                        rtol=0,
                        atol=2e-9,
                    )
                    transformed = actual["calibration"]["transformed"][:, list(self.fixture["protocol"]["predictor_columns"]).index(predictor)]
                    np.testing.assert_allclose(
                        transformed,
                        expected["calibration"]["transformed"][predictor],
                        rtol=0,
                        atol=2e-9,
                    )
                    val_transformed = actual["validation"]["transformed"][:, list(self.fixture["protocol"]["predictor_columns"]).index(predictor)]
                    np.testing.assert_allclose(
                        val_transformed,
                        expected["validation"]["transformed"][predictor],
                        rtol=0,
                        atol=2e-9,
                    )
                for period in ("calibration", "validation"):
                    for name in ("wasp", "baseline"):
                        np.testing.assert_allclose(
                            actual[period][name],
                            expected[period][name],
                            rtol=0,
                            atol=2e-9,
                        )


if __name__ == "__main__":
    unittest.main()
