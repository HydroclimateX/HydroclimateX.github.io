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

    The filter length depends on the wavelet family (db4=8, sym8=16,
    coif3=24, haar=2), so the maximum level is computed for the
    specific wavelet being used.
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
        Wavelet name (e.g., 'db4', 'sym8', 'coif3', 'haar').
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


def variance_modulate(
    details: List[np.ndarray],
    factors: List[float]
) -> List[np.ndarray]:
    """
    Modulate the variance (energy) of each detail band by a multiplicative factor.

    This is the core WASP operation: amplify signal-carrying bands
    and attenuate noise-dominated bands.

    Parameters
    ----------
    details : List[np.ndarray]
        Detail coefficients at each level.
    factors : List[float]
        Multiplicative factors for each level. Must match len(details).
        factor > 1.0 = amplify, factor < 1.0 = attenuate, factor = 1.0 = no change.

    Returns
    -------
    List[np.ndarray]
        Modulated detail coefficients.
    """
    if len(factors) != len(details):
        raise ValueError(
            f"factors length ({len(factors)}) must match details length ({len(details)})"
        )

    modulated = []
    for coeffs, factor in zip(details, factors):
        # Apply sqrt(factor) because variance scales with square of amplitude
        modulated.append(coeffs * np.sqrt(max(0, factor)))
    return modulated


def find_optimal_bands(
    predictor: np.ndarray,
    predictand: np.ndarray,
    wavelet: str = 'db4',
    level: Optional[int] = None,
    mode: str = 'symmetric'
) -> List[float]:
    """
    Identify which frequency bands carry predictive signal by computing
    the correlation between each band's component and the predictand.

    Returns variance modulation factors: >1 for correlated bands,
    <1 for uncorrelated bands.

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
        Modulation factors for each detail level.
    """
    # Align lengths
    min_len = min(len(predictor), len(predictand))
    pred = predictor[:min_len]
    targ = predictand[:min_len]

    # Decompose predictor
    approx, details = wavelet_decompose(pred, wavelet=wavelet, level=level, mode=mode)

    # Zero approximation array matching the coarsest-level coefficient shape.
    # pywt.waverec requires a 1-D array here — a scalar or 0-d array raises AxisError.
    zero_approx = np.zeros_like(approx[0])

    factors = []
    for i, detail_coeffs in enumerate(details):
        # Reconstruct the component corresponding to this detail band only
        # Create zero arrays for other bands
        zero_details = [np.zeros_like(d) for d in details]
        zero_details[i] = detail_coeffs

        component = wavelet_reconstruct(
            [zero_approx],
            zero_details,
            wavelet=wavelet,
            mode=mode
        )

        # Trim to match length
        component = component[:min_len]

        # Compute correlation with predictand
        corr = np.corrcoef(component, targ)[0, 1]
        if np.isnan(corr):
            corr = 0.0

        # Convert correlation to modulation factor:
        # |corr| > 0.15 → amplify (factor > 1)
        # |corr| < 0.05 → attenuate (factor < 1)
        abs_corr = abs(corr)
        if abs_corr > 0.15:
            factor = min(1.0 + abs_corr * 2, 3.0)
        elif abs_corr < 0.05:
            factor = max(1.0 - (0.05 - abs_corr) * 10, 0.1)
        else:
            factor = 1.0

        factors.append(factor)

    return factors
