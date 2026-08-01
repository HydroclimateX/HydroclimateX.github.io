# WASP-Web Example Data

## `demo.csv`

Synthetic monthly hydrologic data (30 years × 12 months = 360 samples).

### Columns

| Column | Description |
|--------|-------------|
| `streamflow_anomaly` | **Predictand** — standardized streamflow anomaly |
| `sst_index` | Predictor — sea surface temperature index |
| `soi` | Predictor — Southern Oscillation Index |
| `pdo_index` | Predictor — Pacific Decadal Oscillation index |
| `precip_index` | Predictor — precipitation index |

### Expected Results

Running WASP on this demo dataset with `db4` wavelet, 20% test split:

| Metric | Baseline (raw) | WASP (spectral) | Improvement |
|--------|---------------|-----------------|-------------|
| NSE | ~0.65–0.75 | ~0.78–0.88 | ✅ |
| Correlation | ~0.82–0.87 | ~0.88–0.94 | ✅ |
| RMSE | ~0.45–0.55 | ~0.32–0.42 | ✅ |

WASP typically outperforms because the synthetic data has known frequency
structure: a decadal oscillation (~120-month period) embedded in the
predictand, which wavelet decomposition can isolate and amplify.

### Usage

1. Upload via WASP-Web UI, or
2. Use the "Load Demo Data" button in the interactive demo, or
3. API: `curl -F "file=@demo.csv" -F "wavelet=db4" http://YOUR_ECS_IP/api/wasp/predict`
