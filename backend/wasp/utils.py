"""
Utility functions for WASP-Web.

Data validation, metrics computation, and helper routines.
"""

import csv
from io import BytesIO, StringIO
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd


MIN_ROWS = 30
MAX_ROWS = 5000
MAX_PREDICTORS = 50
MAX_COLUMNS = MAX_PREDICTORS + 1
MAX_ABS_VALUE = 1e100
SUPPORTED_WAVELETS = frozenset({"db4", "sym8", "coif3", "haar"})


def validate_wavelet(wavelet: str) -> Optional[str]:
    """Return a user-facing error unless the wavelet is supported by the UI."""
    if wavelet not in SUPPORTED_WAVELETS:
        allowed = ", ".join(sorted(SUPPORTED_WAVELETS))
        return f"Unsupported wavelet '{wavelet}'. Choose one of: {allowed}."
    return None


def _preflight_csv(contents: bytes) -> Optional[str]:
    """Reject structurally oversized CSV input before pandas allocates a frame."""
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "CSV files must use UTF-8 encoding."

    try:
        rows = csv.reader(StringIO(text, newline=""))
        header = next(rows, None)
        if header is None:
            return "CSV file is empty."
        column_count = len(header)
        if column_count > MAX_COLUMNS:
            return (
                f"Upload at most {MAX_COLUMNS} columns "
                f"(1 target + at most {MAX_PREDICTORS} predictors)."
            )

        row_count = 0
        for row_count, row in enumerate(rows, start=1):
            if len(row) != column_count:
                return "Every CSV row must contain the same number of columns as the header."
            if row_count > MAX_ROWS:
                return f"Upload at most {MAX_ROWS} rows. Found more than {MAX_ROWS}."
    except (csv.Error, UnicodeError) as error:
        return f"Failed to parse CSV structure: {error}"

    return None


def validate_data(
    contents: bytes,
    filename: str
) -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Validate uploaded CSV data and return a DataFrame.

    Parameters
    ----------
    contents : bytes
        Raw file contents.
    filename : str
        Original filename (used to determine format).

    Returns
    -------
    df : pd.DataFrame
        Parsed and validated DataFrame.
    error : str or None
        Error message if validation fails, None otherwise.

    Resource and modelling checks are intentionally strict because this endpoint
    runs on a 2 GB server. Every value must be numeric and finite; the target and
    every retained predictor must vary.
    """
    if not filename.lower().endswith('.csv'):
        return pd.DataFrame(), "Only CSV files are supported (.csv)"

    preflight_error = _preflight_csv(contents)
    if preflight_error:
        return pd.DataFrame(), preflight_error

    try:
        df = pd.read_csv(BytesIO(contents))
    except Exception as e:
        return pd.DataFrame(), f"Failed to parse CSV: {str(e)}"

    if len(df.columns) < 2:
        return pd.DataFrame(), (
            f"Need at least 2 columns (1 predictand + ≥1 predictors). "
            f"Found {len(df.columns)}."
        )

    n_predictors = len(df.columns) - 1
    if n_predictors > MAX_PREDICTORS:
        return pd.DataFrame(), (
            f"Upload at most {MAX_PREDICTORS} predictors. Found {n_predictors}."
        )

    if len(df) > MAX_ROWS:
        return pd.DataFrame(), (
            f"Upload at most {MAX_ROWS} rows. Found {len(df)}."
        )

    if len(df) < MIN_ROWS:
        return pd.DataFrame(), (
            f"Need at least {MIN_ROWS} rows for meaningful wavelet decomposition. "
            f"Found {len(df)}."
        )

    try:
        df = df.apply(pd.to_numeric, errors="raise")
    except (TypeError, ValueError):
        return pd.DataFrame(), "Every CSV column must contain only numeric values."

    values = df.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        return pd.DataFrame(), "Every CSV value must be finite; missing and infinite values are not accepted."
    if np.any(np.abs(values) > MAX_ABS_VALUE):
        return pd.DataFrame(), (
            f"Every CSV value's absolute value must not exceed {MAX_ABS_VALUE:g}."
        )

    target = df.iloc[:, 0]
    if target.nunique(dropna=False) <= 1:
        return pd.DataFrame(), "Target column must be non-constant."

    constant_predictors = [
        str(column)
        for column in df.columns[1:]
        if df[column].nunique(dropna=False) <= 1
    ]
    if constant_predictors:
        names = ", ".join(constant_predictors)
        return pd.DataFrame(), f"Each predictor must be non-constant. Constant predictor(s): {names}."

    return df, None


def make_json_safe(value: Any) -> Any:
    """Recursively normalise numpy and non-finite values for strict JSON output."""
    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return make_json_safe(value.tolist())
    if isinstance(value, np.generic):
        return make_json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def compute_metrics(
    observed: np.ndarray,
    predicted: np.ndarray
) -> Dict[str, object]:
    """
    Compute standard hydrologic forecast evaluation metrics.

    Parameters
    ----------
    observed : np.ndarray
        Observed values.
    predicted : np.ndarray
        Predicted values.

    Returns
    -------
    Dict with keys:
        mse, rmse, mae, nse, kge, correlation, pod, far, csi
    """
    # Align lengths
    min_len = min(len(observed), len(predicted))
    obs = np.asarray(observed[:min_len], dtype=float)
    pred = np.asarray(predicted[:min_len], dtype=float)

    # Public metrics must remain JSON-safe even when this helper is reused with
    # data that did not pass through the upload validator.
    mask = np.isfinite(obs) & np.isfinite(pred)
    obs = obs[mask]
    pred = pred[mask]

    if len(obs) < 3:
        return {'error': 'Too few valid data points'}

    def rounded_finite(value: float, digits: int) -> Optional[float]:
        numeric = float(value)
        return round(numeric, digits) if np.isfinite(numeric) else None

    # Basic metrics. Extremely large finite inputs can still overflow during
    # arithmetic, so every derived value is normalised at the response boundary.
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        residuals = obs - pred
        mse = float(np.mean(residuals ** 2))
        rmse = float(np.sqrt(mse))
        mae = float(np.mean(np.abs(residuals)))
        # Nash-Sutcliffe Efficiency
        obs_mean = np.mean(obs)
        ss_res = np.sum(residuals ** 2)
        ss_tot = np.sum((obs - obs_mean) ** 2)
        nse = float(1 - ss_res / ss_tot) if ss_tot > 0 else None

    # Kling-Gupta Efficiency (KGE)
    obs_std = float(np.std(obs))
    pred_std = float(np.std(pred))
    obs_mean = float(np.mean(obs))
    r = (
        float(np.corrcoef(obs, pred)[0, 1])
        if obs_std > 0 and pred_std > 0
        else None
    )
    alpha = pred_std / obs_std if obs_std > 0 else None
    beta = float(np.mean(pred)) / obs_mean if obs_mean != 0 else None
    if r is not None and alpha is not None and beta is not None:
        kge = float(1 - np.sqrt((r - 1)**2 + (alpha - 1)**2 + (beta - 1)**2))
    else:
        kge = None

    # Extreme event detection metrics (using 90th percentile threshold)
    threshold = np.percentile(obs, 90)
    obs_event = obs >= threshold
    pred_event = pred >= threshold
    hits = np.sum(obs_event & pred_event)
    false_alarms = np.sum(~obs_event & pred_event)
    misses = np.sum(obs_event & ~pred_event)

    pod = float(hits / (hits + misses)) if (hits + misses) > 0 else float('nan')
    far = float(false_alarms / (hits + false_alarms)) if (hits + false_alarms) > 0 else float('nan')
    csi = float(hits / (hits + misses + false_alarms)) if (hits + misses + false_alarms) > 0 else float('nan')

    return {
        'mse': rounded_finite(mse, 6),
        'rmse': rounded_finite(rmse, 4),
        'mae': rounded_finite(mae, 4),
        'nse': rounded_finite(nse, 4) if nse is not None else None,
        'kge': rounded_finite(kge, 4) if kge is not None else None,
        'correlation': rounded_finite(r, 4) if r is not None else None,
        'pod': rounded_finite(pod, 4),
        'far': rounded_finite(far, 4),
        'csi': rounded_finite(csi, 4),
        'n_samples': len(obs),
    }


def make_demo_csv() -> bytes:
    """
    Generate a synthetic demo dataset for testing WASP-Web.
    Contains a predictand (streamflow anomaly) and 4 climate predictors.
    """
    np.random.seed(42)
    n = 360  # 30 years of monthly data

    t = np.arange(n)

    # Predictand: composite of low-freq + high-freq signals + noise
    y = (
        2.0 * np.sin(2 * np.pi * t / 120) +     # decadal oscillation
        0.8 * np.sin(2 * np.pi * t / 12) +      # annual cycle
        0.3 * np.sin(2 * np.pi * t / 6) +       # semi-annual
        0.5 * np.random.randn(n)
    )

    # Predictors: correlated versions with different noise levels
    x1 = 0.7 * y + 0.3 * np.random.randn(n)      # Strong predictor (SST index)
    x2 = 0.5 * y + 0.5 * np.random.randn(n)      # Moderate predictor (SOI)
    x3 = 0.3 * np.random.randn(n) + 0.4 * y       # Weaker predictor (PDO-like)
    x4 = np.sin(2 * np.pi * t / 120 + 1.5) + 0.5 * np.random.randn(n)  # Phase-shifted

    df = pd.DataFrame({
        'streamflow_anomaly': y,
        'sst_index': x1,
        'soi': x2,
        'pdo_index': x3,
        'precip_index': x4,
    })

    buf = BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()
