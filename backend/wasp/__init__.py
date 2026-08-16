"""
WASP — WAvelet System Prediction
=================================
Core wavelet-based spectral transformation for hydrologic prediction.

Reference:
  Jiang, Z., Sharma, A. & Johnson, F. (2020).
  Refining predictor spectral representation using wavelet theory
  for improved natural system modelling. Water Resources Research.

Author: HydroclimateX Lab
Date: 2026-08-01
"""

from .wavelet import wavelet_decompose, wavelet_reconstruct
from .utils import validate_data, compute_metrics

__all__ = ['wavelet_decompose', 'wavelet_reconstruct', 'validate_data', 'compute_metrics']
