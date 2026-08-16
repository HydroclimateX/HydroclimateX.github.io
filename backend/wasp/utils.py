"""
Utility functions for WASP-Web.

Data validation, metrics computation, and helper routines.
"""

import numpy as np
import pandas as pd
from io import BytesIO
from typing import Tuple, Dict, Optional


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

    Checks:
    - File is CSV
    - At least 2 columns
    - At least 30 rows (minimum for meaningful wavelet decomposition)
    - No all-NaN columns
    """
    if not filename.lower().endswith('.csv'):
        return pd.DataFrame(), "Only CSV files are supported (.csv)"

    try:
        df = pd.read_csv(BytesIO(contents))
    except Exception as e:
        return pd.DataFrame(), f"Failed to parse CSV: {str(e)}"

    if len(df.columns) < 2:
        return pd.DataFrame(), (
            f"Need at least 2 columns (1 predictand + ≥1 predictors). "
            f"Found {len(df.columns)}."
        )

    if len(df) < 30:
        return pd.DataFrame(), (
            f"Need at least 30 rows for meaningful wavelet decomposition. "
            f"Found {len(df)}."
        )

    # Drop all-NaN columns
    df = df.dropna(axis=1, how='all')

    # Drop rows where predictand (first column assumed) is NaN
    df = df.dropna(subset=[df.columns[0]])

    if len(df) < 30:
        return pd.DataFrame(), (
            f"After removing NaN values, only {len(df)} rows remain. Need ≥ 30."
        )

    return df, None


def compute_metrics(
    observed: np.ndarray,
    predicted: np.ndarray
) -> Dict[str, float]:
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

    # Remove NaN
    mask = ~(np.isnan(obs) | np.isnan(pred))
    obs = obs[mask]
    pred = pred[mask]

    if len(obs) < 3:
        return {'error': 'Too few valid data points'}

    # Basic metrics
    residuals = obs - pred
    mse = float(np.mean(residuals ** 2))
    rmse = float(np.sqrt(mse))
    mae = float(np.mean(np.abs(residuals)))

    # Nash-Sutcliffe Efficiency
    obs_mean = np.mean(obs)
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((obs - obs_mean) ** 2)
    nse = float(1 - ss_res / ss_tot) if ss_tot > 0 else float('nan')

    # Kling-Gupta Efficiency (KGE)
    r = np.corrcoef(obs, pred)[0, 1]
    alpha = np.std(pred) / np.std(obs) if np.std(obs) > 0 else 0
    beta = np.mean(pred) / np.mean(obs) if np.mean(obs) != 0 else 0
    kge = float(1 - np.sqrt((r - 1)**2 + (alpha - 1)**2 + (beta - 1)**2))

    # Correlation
    correlation = float(r) if not np.isnan(r) else 0.0

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
        'mse': round(mse, 6),
        'rmse': round(rmse, 4),
        'mae': round(mae, 4),
        'nse': round(nse, 4),
        'kge': round(kge, 4),
        'correlation': round(correlation, 4),
        'pod': round(pod, 4) if not np.isnan(pod) else None,
        'far': round(far, 4) if not np.isnan(far) else None,
        'csi': round(csi, 4) if not np.isnan(csi) else None,
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
