"""
Wavelet transform module for WASP.

Implements Discrete Wavelet Transform (DWT) decomposition and
reconstruction using PyWavelets, supporting multiple wavelet families.

The core WASP insight: decompose predictor time series into frequency
bands, then selectively modulate variance in the bands that carry
predictive signal before reconstructing — producing spectrally
refined predictors that improve forecast skill.
"""

import numpy as np
import pywt
from typing import Tuple, List, Optional, Dict


# Supported wavelet families
WAVELET_FAMILIES = {
    'db': 'Daubechies',
    'sym': 'Symlets',
    'coif': 'Coiflets',
    'bior': 'Biorthogonal',
    'dmey': 'Discrete Meyer',
    'haar': 'Haar',
}

def max_levels(n: int, wavelet: str = 'db4') -> int:
    """Return max decomposition levels for series of length n.

    The filter length depends on the selected Daubechies wavelet, so the
    maximum level is computed for the specific wavelet being used.
    """
    return pywt.dwt_max_level(n, filter_len=pywt.Wavelet(wavelet).dec_len)


def wavelet_decompose(
    data: np.ndarray,
    wavelet: str = 'db4',
    level: Optional[int] = None,
    mode: str = 'symmetric'
) -> Tuple[List[np.ndarray], List[np.ndarray]]:
    """
    Decompose a 1-D time series using Discrete Wavelet Transform (DWT).

    The series is decomposed into approximation coefficients at the
    coarsest level and detail coefficients at each level, representing
    progressively finer frequency bands.

    Parameters
    ----------
    data : np.ndarray (n,)
        1-D input time series. Length n must be ≥ 2^level.
    wavelet : str
        Wavelet name (the public API allows db1, db2, db4, db8, and db16).
    level : int, optional
        Number of decomposition levels. Default: max possible for length.
    mode : str
        Signal extension mode for boundary handling ('symmetric', 'zero',
        'periodic', 'reflect').

    Returns
    -------
    coeffs_approx : List[np.ndarray]
        Approximation coefficients at the final level (1 element).
    coeffs_detail : List[np.ndarray]
        Detail coefficients at each level (length = level).
        coeffs_detail[0] = finest scale, coeffs_detail[-1] = coarsest.

    Example
    -------
    >>> approx, details = wavelet_decompose(np.random.randn(256))
    >>> len(details)  # number of detail levels
    >>> details[0].shape  # finest detail coefficients
    """
    n = len(data)
    if level is None:
        level = max_levels(n, wavelet=wavelet)
    else:
        level = min(level, max_levels(n, wavelet=wavelet))

    # Validate wavelet
    try:
        w = pywt.Wavelet(wavelet)
    except ValueError:
        raise ValueError(
            f"Unknown wavelet '{wavelet}'. Available: {list(WAVELET_FAMILIES.keys())}"
        )

    # Perform DWT decomposition
    coeffs = pywt.wavedec(data, wavelet=wavelet, level=level, mode=mode)

    # coeffs[0] = approximation, coeffs[1:] = details (finest → coarsest)
    approx = [coeffs[0]]
    details = coeffs[1:]

    return approx, details


def wavelet_reconstruct(
    approx: List[np.ndarray],
    details: List[np.ndarray],
    wavelet: str = 'db4',
    mode: str = 'symmetric'
) -> np.ndarray:
    """
    Reconstruct a time series from wavelet coefficients.

    Parameters
    ----------
    approx : List[np.ndarray]
        Approximation coefficients (typically 1 element).
    details : List[np.ndarray]
        Detail coefficients from finest to coarsest.
    wavelet : str
        Same wavelet used for decomposition.
    mode : str
        Same mode used for decomposition.

    Returns
    -------
    np.ndarray
        Reconstructed time series.
    """
    coeffs = [approx[0]] + list(details)
    return pywt.waverec(coeffs, wavelet=wavelet, mode=mode)


def get_band_energy(
    details: List[np.ndarray],
    approx: Optional[List[np.ndarray]] = None
) -> Dict[str, float]:
    """
    Compute the relative energy in each frequency band.

    Energy is proportional to variance: E = sum(c²) / total_energy.

    Returns
    -------
    Dict[str, float]
        Keys like 'D1' (finest detail), 'D2', ..., 'A{level}' (approximation).
        Values are the fraction of total energy in each band.
    """
    total_energy = 0.0
    band_energy = {}

    for i, d in enumerate(details):
        e = np.sum(d ** 2)
        band_energy[f'D{i+1}'] = e
        total_energy += e

    if approx is not None:
        for i, a in enumerate(approx):
            e = np.sum(a ** 2)
            band_energy[f'A{len(details)}'] = e
            total_energy += e

    if total_energy > 0:
        for k in band_energy:
            band_energy[k] /= total_energy

    return band_energy


def _band_components(
    data: np.ndarray,
    wavelet: str = 'db4',
    level: Optional[int] = None,
    mode: str = 'symmetric'
) -> np.ndarray:
    """
    Reconstruct each wavelet band as an individual subseries.

    Returns a (n, level + 1) array whose columns are the separately
    reconstructed subseries for the D1..D{level} detail bands followed by
    the A{level} approximation band. Because the DWT reconstruction is
    linear, the columns sum back to the original (centred) series.

    Parameters
    ----------
    data : np.ndarray (n,)
        1-D input time series.
    wavelet : str
        Wavelet name.
    level : int, optional
        Decomposition level.
    mode : str
        Boundary mode.

    Returns
    -------
    np.ndarray
        Band subseries with shape (n, level + 1), columns ordered
        D1, D2, ..., D{level}, A{level}.
    """
    n = len(data)
    approx, details = wavelet_decompose(data, wavelet=wavelet, level=level, mode=mode)

    # Zero approximation array matching the coarsest-level coefficient shape.
    # pywt.waverec requires a 1-D array here — a scalar or 0-d array raises AxisError.
    zero_approx = np.zeros_like(approx[0])

    components = []
    for i, detail_coeffs in enumerate(details):
        # Reconstruct this detail band only, with all other bands zeroed.
        zero_details = [np.zeros_like(d) for d in details]
        zero_details[i] = detail_coeffs
        component = wavelet_reconstruct(
            [zero_approx], zero_details, wavelet=wavelet, mode=mode
        )
        components.append(component[:n])

    # Approximation band: reconstruct with all detail bands zeroed.
    zero_details = [np.zeros_like(d) for d in details]
    approx_comp = wavelet_reconstruct(approx, zero_details, wavelet=wavelet, mode=mode)
    components.append(approx_comp[:n])

    return np.column_stack(components)


def _standardize_bands(B: np.ndarray) -> np.ndarray:
    """
    Standardize each band subseries to zero mean and unit variance.

    Bands whose standard deviation is below EPS are treated as constant
    (they carry no signal and would otherwise amplify noise). Uses ddof=0
    (population standard deviation) with an epsilon guard, matching the
    reference Python implementation (HydroclimateX/WASP_python, wasp()/
    wasp_val()).
    """
    EPS = 1e-8
    Bn = np.empty_like(B, dtype=float)
    for j in range(B.shape[1]):
        col = B[:, j]
        sd = col.std(ddof=0)
        Bn[:, j] = (col - col.mean()) / (sd if sd >= EPS else 1.0)
    return Bn


def covariance_modulation_factors(
    predictor: np.ndarray,
    predictand: np.ndarray,
    wavelet: str = 'db4',
    level: Optional[int] = None,
    mode: str = 'symmetric'
) -> List[float]:
    """
    Compute variance modulation factors from the covariance between each
    standardized frequency band of the predictor and the predictand.

    Implements Equation 10 of Jiang, Sharma & Johnson (2020), Water Resources
    Research, 56(3), e2019WR026962 — Refining predictor spectral representation
    using wavelet theory for improved natural system modeling. Factors cover
    every frequency band: the D1..D{level} detail bands AND the A{level}
    approximation band.

    The factor for band j is

        alpha_j = cov(x, Bn_j) / ||cov(x, Bn)||_2

    where Bn_j is the predictor's band-j subseries standardized to unit
    variance. Because sum(alpha_j^2) = 1, applying the factors preserves the
    total variance of the predictor while re-weighting it across bands.

    Parameters
    ----------
    predictor : np.ndarray
        Predictor time series.
    predictand : np.ndarray
        Predictand (target) time series of same length.
    wavelet : str
        Wavelet name for decomposition.
    level : int, optional
        Decomposition level.
    mode : str
        Boundary mode.

    Returns
    -------
    List[float]
        Signed modulation factors for each band, ordered
        D1, D2, ..., D{level}, A{level} (length level + 1).
    """
    # Align lengths
    min_len = min(len(predictor), len(predictand))
    dp_c = predictor[:min_len] - predictor[:min_len].mean()
    targ = predictand[:min_len]

    # Decompose and reconstruct each band, then standardize to unit variance.
    B = _band_components(dp_c, wavelet=wavelet, level=level, mode=mode)
    Bn = _standardize_bands(B)

    # Covariance of the predictand with each standardized band (cov auto-centers).
    covs = np.array([
        np.cov(targ, Bn[:, j])[0, 1] for j in range(Bn.shape[1])
    ])

    norm2 = np.linalg.norm(covs)
    if norm2 == 0:
        # Degenerate case: no band covaries with the predictand. Uniform
        # weights 1/sqrt(k) keep sum(alpha^2) = 1 so variance is preserved.
        factors = np.full(Bn.shape[1], 1.0 / np.sqrt(Bn.shape[1]))
    else:
        factors = covs / norm2

    return [float(f) for f in factors]


def variance_transform(
    predictor: np.ndarray,
    factors: List[float],
    wavelet: str = 'db4',
    level: Optional[int] = None,
    mode: str = 'symmetric'
) -> np.ndarray:
    """
    Apply the covariance-based variance transformation to a predictor.

    Reconstructs the predictor's band subseries, standardizes each to unit
    variance, and re-combines them weighted by the (calibration) factors:

        dp.n = Bn %*% (factors * sd(predictor)) + mean(predictor)

    This is the variance transformation of Jiang et al. (2020), Equation 10.
    For a held-out segment, pass the factors computed on calibration data —
    the bands are re-standardized on the segment and scaled by its own
    standard deviation, matching dwt.vt.val in the reference R package.

    Parameters
    ----------
    predictor : np.ndarray
        Predictor time series to transform.
    factors : List[float]
        Variance modulation factors (length level + 1), e.g. from
        covariance_modulation_factors().
    wavelet : str
        Wavelet name.
    level : int, optional
        Decomposition level.
    mode : str
        Boundary mode.

    Returns
    -------
    np.ndarray
        Variance-transformed predictor of the same length as predictor.
    """
    dp_c = predictor - predictor.mean()
    B = _band_components(dp_c, wavelet=wavelet, level=level, mode=mode)
    Bn = _standardize_bands(B)
    scale = float(np.std(predictor, ddof=1))
    return Bn @ (np.asarray(factors, dtype=float) * scale) + predictor.mean()
