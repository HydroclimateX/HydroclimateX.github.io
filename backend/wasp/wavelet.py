"""
Wavelet transform module for WASP.

Implements the wavelet multiresolution analysis (MRA) used by the reference
R package: a periodic (circular) DWT on a zero-padded, dyadic-length series,
with band subseries reconstructed via the adjoint transform — matching
waveslim::mra() exactly (Jiang, Sharma & Johnson 2020).

The core WASP insight: decompose predictor time series into frequency
bands, then selectively modulate variance in the bands that carry
predictive signal before reconstructing — producing spectrally
refined predictors that improve forecast skill.
"""

import numpy as np
import pywt
from scipy import stats
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
    wavelet: str = 'db16',
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


def get_band_energy(B: np.ndarray) -> Dict[str, float]:
    """
    Compute the relative energy (variance) in each frequency band.

    B is the MRA band subseries matrix from `_band_components`, columns
    ordered D1..D{level}, A{level}. Energy per band is its variance, and the
    returned values are the share of the total.

    Returns
    -------
    Dict[str, float]
        Keys like 'D1' (finest detail), 'D2', ..., 'A{level}' (approximation).
        Values are the fraction of total band variance in each band.
    """
    band_var = np.var(B, axis=0)
    total = band_var.sum() or 1.0
    level = B.shape[1] - 1
    out = {f'D{i+1}': float(band_var[i] / total) for i in range(level)}
    out[f'A{level}'] = float(band_var[-1] / total)
    return out


def _waveslim_filters(wavelet: str) -> Tuple[np.ndarray, np.ndarray]:
    """
    Return the (high-pass, low-pass) filter taps in waveslim's orientation.

    waveslim's `hpf`/`lpf` are PyWavelets' `dec_hi`/`dec_lo` reversed, so a
    single pywt wavelet name yields the exact taps the R package uses.
    """
    w = pywt.Wavelet(wavelet)
    h = np.asarray(w.dec_hi[::-1], dtype=float)
    g = np.asarray(w.dec_lo[::-1], dtype=float)
    return h, g


def _circ_dwt(x: np.ndarray, h: np.ndarray, g: np.ndarray, level: int) -> List[np.ndarray]:
    """
    One- and multi-level periodic (circular) DWT, waveslim convention.

    At each level, `W[k] = sum_l h[l] x[(2k - l + 1) mod M]` and
    `V[k] = sum_l g[l] x[(2k - l + 1) mod M]`. Returns coefficient lists
    `[d1, ..., dJ, sJ]` (d1 = finest detail, sJ = coarsest approximation).
    """
    out: List[np.ndarray] = []
    cur = x
    for _ in range(level):
        M, half, L = len(cur), len(cur) // 2, len(h)
        idx = (2 * np.arange(half)[:, None] - np.arange(L)[None, :] + 1) % M
        out.append(np.sum(h[None, :] * cur[idx], axis=1))
        cur = np.sum(g[None, :] * cur[idx], axis=1)
    out.append(cur)
    return out


def _circ_idwt(coeffs: List[np.ndarray], h: np.ndarray, g: np.ndarray) -> np.ndarray:
    """
    Inverse (adjoint) of `_circ_dwt`, waveslim convention.

    `x[m] = sum_k (W[k] h[(2k - m + 1) mod M] + V[k] g[(2k - m + 1) mod M])`,
    with the filters zero-padded to length M. Reconstructs from the full
    coefficient list `[d1, ..., dJ, sJ]`; zero out a band to isolate it.
    """
    J = len(coeffs) - 1
    cur = coeffs[-1]
    for j in range(J, 0, -1):
        W, V = coeffs[j - 1], cur
        M, L = 2 * len(W), len(h)
        hpad = np.zeros(M)
        hpad[:L] = h
        gpad = np.zeros(M)
        gpad[:L] = g
        m = np.arange(M)[:, None]
        idx = (2 * np.arange(len(W))[None, :] - m + 1) % M
        cur = (W[None, :] * hpad[idx]).sum(axis=1) + (V[None, :] * gpad[idx]).sum(axis=1)
    return cur


def _band_components(
    data: np.ndarray,
    wavelet: str = 'db4',
    level: Optional[int] = None
) -> np.ndarray:
    """
    Reconstruct each wavelet band as an individual subseries.

    Mirrors the R package: the centred series is zero-padded to dyadic length,
    decomposed with a periodic circular DWT (waveslim::mra), each band
    reconstructed separately, then cropped back to the original length.

    Returns a (n, level + 1) array whose columns are the separately
    reconstructed subseries for the D1..D{level} detail bands followed by
    the A{level} approximation band.

    Parameters
    ----------
    data : np.ndarray (n,)
        1-D input time series.
    wavelet : str
        Wavelet name.
    level : int, optional
        Decomposition level.

    Returns
    -------
    np.ndarray
        Band subseries with shape (n, level + 1), columns ordered
        D1, D2, ..., D{level}, A{level}.
    """
    n = len(data)
    N = 1 << int(np.ceil(np.log2(max(n, 2))))
    x_pad = np.concatenate([data, np.zeros(N - n)])

    max_j = int(np.floor(np.log2(N)))
    if level is None:
        level = max_j
    else:
        level = max(min(level, max_j), 1)

    h, g = _waveslim_filters(wavelet)
    coeffs = _circ_dwt(x_pad, h, g, level)

    components = []
    for j in range(1, level + 1):
        z = [np.zeros_like(c) for c in coeffs]
        z[j - 1] = coeffs[j - 1]
        components.append(_circ_idwt(z, h, g)[:n])

    z = [np.zeros_like(c) for c in coeffs]
    z[-1] = coeffs[-1]
    components.append(_circ_idwt(z, h, g)[:n])

    return np.column_stack(components)


def _standardize_bands(B: np.ndarray) -> np.ndarray:
    """
    Standardize each band subseries to zero mean and unit variance.

    Bands whose standard deviation is below EPS are treated as constant
    (they carry no signal and would otherwise amplify noise). Uses ddof=1
    (sample standard deviation), matching R's `scale()`/`sd()` in the
    reference package.
    """
    EPS = 1e-8
    Bn = np.empty_like(B, dtype=float)
    for j in range(B.shape[1]):
        col = B[:, j]
        sd = col.std(ddof=1)
        Bn[:, j] = (col - col.mean()) / (sd if sd >= EPS else 1.0)
    return Bn


def covariance_modulation_factors(
    predictor: np.ndarray,
    predictand: np.ndarray,
    wavelet: str = 'db4',
    level: Optional[int] = None
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

    The overall sign of the factors follows R's cov.opt="auto": the factors
    are negated when the variance-transformed predictor is significantly
    negatively correlated with the original predictor (correlation < 0 with
    two-sided p < 0.05), keeping the transformation aligned with the input.

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
    B = _band_components(dp_c, wavelet=wavelet, level=level)
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

    # R cov.opt="auto": flip the sign if the transformed predictor is
    # significantly negatively correlated with the original predictor.
    if min_len > 2:
        dp_n = Bn @ (factors * float(np.std(predictor[:min_len], ddof=1))) \
            + predictor[:min_len].mean()
        r = np.corrcoef(dp_n, predictor[:min_len])[0, 1]
        if np.isfinite(r) and r < 0:
            t_stat = r * np.sqrt((min_len - 2) / (1 - r * r))
            p_value = 2.0 * stats.t.sf(abs(t_stat), min_len - 2)
            if p_value < 0.05:
                factors = -factors

    return [float(f) for f in factors]


def variance_transform(
    predictor: np.ndarray,
    factors: List[float],
    wavelet: str = 'db4',
    level: Optional[int] = None
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

    Returns
    -------
    np.ndarray
        Variance-transformed predictor of the same length as predictor.
    """
    dp_c = predictor - predictor.mean()
    B = _band_components(dp_c, wavelet=wavelet, level=level)
    Bn = _standardize_bands(B)
    scale = float(np.std(predictor, ddof=1))
    return Bn @ (np.asarray(factors, dtype=float) * scale) + predictor.mean()
