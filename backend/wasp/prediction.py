"""
WASP Prediction Engine.

Orchestrates the full WASP workflow:
1. Data ingestion & validation
2. Wavelet decomposition of each predictor
3. Optimal frequency band identification
4. Variance modulation
5. Predictor reconstruction
6. Model fitting (Ridge regression)
7. Evaluation & metrics
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit

from .wavelet import (
    wavelet_decompose,
    wavelet_reconstruct,
    find_optimal_bands,
    variance_modulate,
    get_band_energy,
    max_levels,
)
from .utils import compute_metrics, make_json_safe, validate_data, validate_wavelet


def run_wasp_prediction(
    contents: bytes,
    filename: str,
    wavelet: str = 'db4',
    level: Optional[int] = None,
    test_size: float = 0.2,
    alpha: float = 1.0,
) -> Dict[str, Any]:
    """
    Run the complete WASP prediction pipeline.

    Parameters
    ----------
    contents : bytes
        Raw CSV file contents.
    filename : str
        Original filename.
    wavelet : str
        Wavelet family (db4, sym8, coif3, haar, etc.).
    level : int, optional
        Decomposition level. Auto-detected if None.
    test_size : float
        Fraction of data to hold out for testing (0.0 to 0.5).
    alpha : float
        Ridge regression regularization strength.

    Returns
    -------
    Dict with keys:
        success, metrics, band_energies, modulation_factors,
        plots, message, n_samples, n_predictors, wavelet, level
    """
    # 1. Validate the bounded public parameter set, then parse the CSV.
    wavelet_error = validate_wavelet(wavelet)
    if wavelet_error:
        return {'success': False, 'message': wavelet_error}

    df, error = validate_data(contents, filename)
    if error:
        return {'success': False, 'message': error}

    # 2. Extract predictand (first column) and predictors (remaining)
    target_col = df.columns[0]
    predictor_cols = df.columns[1:].tolist()

    y = df[target_col].values.astype(float)
    X_raw = df[predictor_cols].values.astype(float)

    n_samples = len(y)
    n_predictors = len(predictor_cols)

    # 3. Train/test split (temporal — last test_size fraction)
    split_idx = int(n_samples * (1 - test_size))
    y_train, y_test = y[:split_idx], y[split_idx:]
    X_train_raw, X_test_raw = X_raw[:split_idx], X_raw[split_idx:]

    # Choose a decomposition level that BOTH train and test can support.
    # The test segment is shorter, so its max DWT level is the binding
    # constraint. Using a level the test set can't reach would make the
    # modulation factors (fit on train) mismatch the test decomposition.
    max_level_train = max_levels(len(y_train), wavelet=wavelet)
    max_level_test = max_levels(len(y_test), wavelet=wavelet)
    if level is None:
        level = min(max_level_train, max_level_test, 6)  # cap at 6 for performance
    else:
        level = min(level, max_level_train, max_level_test, 6)

    # 4. WASP spectral transformation on each predictor (fit on train)
    X_train_transformed = np.zeros_like(X_train_raw)
    X_test_transformed = np.zeros_like(X_test_raw)

    band_energies = {}
    modulation_factors = {}

    for i, col in enumerate(predictor_cols):
        # Find optimal modulation factors from training data
        factors = find_optimal_bands(
            X_train_raw[:, i], y_train,
            wavelet=wavelet, level=level
        )
        modulation_factors[col] = [round(f, 3) for f in factors]

        # Transform training predictor
        approx, details = wavelet_decompose(
            X_train_raw[:, i], wavelet=wavelet, level=level
        )
        energies = get_band_energy(details, approx)
        band_energies[col] = energies

        modulated_details = variance_modulate(details, factors)
        X_train_transformed[:, i] = wavelet_reconstruct(
            approx, modulated_details, wavelet=wavelet
        )[:len(y_train)]

        # Transform test predictor (using same factors from train)
        approx_t, details_t = wavelet_decompose(
            X_test_raw[:, i], wavelet=wavelet, level=level
        )
        modulated_details_t = variance_modulate(details_t, factors)
        X_test_transformed[:, i] = wavelet_reconstruct(
            approx_t, modulated_details_t, wavelet=wavelet
        )[:len(y_test)]

    # 5. Fit model on spectrally transformed data
    scaler_X = StandardScaler()
    scaler_y = StandardScaler()

    X_train_scaled = scaler_X.fit_transform(X_train_transformed)
    y_train_scaled = scaler_y.fit_transform(y_train.reshape(-1, 1)).ravel()

    model = Ridge(alpha=alpha)
    model.fit(X_train_scaled, y_train_scaled)

    # 6. Predict on test set
    X_test_scaled = scaler_X.transform(X_test_transformed)
    y_pred_scaled = model.predict(X_test_scaled)
    y_pred = scaler_y.inverse_transform(y_pred_scaled.reshape(-1, 1)).ravel()

    # 7. Also compute baseline: raw predictors without spectral transformation
    baseline_scaler_X = StandardScaler()
    baseline_scaler_y = StandardScaler()
    X_train_raw_scaled = baseline_scaler_X.fit_transform(X_train_raw)
    X_test_raw_scaled = baseline_scaler_X.transform(X_test_raw)
    y_train_raw_scaled = baseline_scaler_y.fit_transform(y_train.reshape(-1, 1)).ravel()
    baseline_model = Ridge(alpha=alpha)
    baseline_model.fit(
        X_train_raw_scaled,
        y_train_raw_scaled
    )
    y_baseline_pred = baseline_model.predict(X_test_raw_scaled)
    y_baseline_pred = baseline_scaler_y.inverse_transform(
        y_baseline_pred.reshape(-1, 1)
    ).ravel()

    # 8. Compute metrics
    wasp_metrics = compute_metrics(y_test, y_pred)
    baseline_metrics = compute_metrics(y_test, y_baseline_pred)

    # 9. Model coefficients
    coef_df = []
    for i, col in enumerate(predictor_cols):
        coef_df.append({
            'predictor': col,
            'coefficient': round(float(model.coef_[i]), 4),
        })

    return make_json_safe({
        'success': True,
        'metrics': {
            'wasp': wasp_metrics,
            'baseline': baseline_metrics,
        },
        'model_coefficients': coef_df,
        'band_energies': band_energies,
        'modulation_factors': modulation_factors,
        'n_samples': n_samples,
        'n_predictors': n_predictors,
        'n_train': len(y_train),
        'n_test': len(y_test),
        'wavelet': wavelet,
        'level': level,
        'predictor_columns': predictor_cols,
        'target_column': target_col,
        'predictions': {
            'observed': [round(float(v), 4) for v in y_test],
            'wasp_predicted': [round(float(v), 4) for v in y_pred],
            'baseline_predicted': [round(float(v), 4) for v in y_baseline_pred],
        },
    })
