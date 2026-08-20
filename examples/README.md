# WASP example data

The research-team homepage is `https://hydroclimatex.com`; methodology and file
requirements are documented at
`https://hydroclimatex.com/showcase/wasp-web/`; the interactive application runs
at `https://wasp.hydroclimatex.com` on the Alibaba Cloud Hong Kong Lightweight
Application Server.

## `demo.csv`

This file contains 391 monthly observations. The browser initially selects the
first column as Y and the remaining four columns as X, but those roles can be
changed before a run.

| Column | Role |
|---|---|
| `streamflow_anomaly` | Target: standardised streamflow anomaly |
| `sst_index` | Sea-surface-temperature predictor |
| `soi` | Southern Oscillation Index predictor |
| `pdo_index` | Pacific Decadal Oscillation predictor |
| `precip_index` | Precipitation predictor |

Selected Y/X columns must be numeric, finite, and non-constant; unused text,
date, or identifier columns are ignored. Files must contain 30–5000 rows and
1–50 selected predictors, with a resource limit of 51 total columns including
unused columns. Nginx accepts an 11 MB multipart request, and FastAPI enforces
a 10 MiB limit on the uploaded file itself. Supported wavelets are
`db1` (Haar), `db2`, `db4`, `db8`, and `db16` (Python-only — no upstream
R/waveslim equivalent). Available models are Linear Regression, K-Nearest
Neighbors, and XGBoost.

## Canonical R parity benchmark

`wasp_demo.csv` is the canonical 1,200-row parity fixture: use the first 600
rows for training and the remaining 600 for testing. The shared Python/R
wavelet mapping is `db1`→`haar`, `db2`→`d4`, `db4`→`d8`, and `db8`→`d16`;
`db16` remains Python-only with no upstream R/waveslim equivalent.

## Use the example

In the browser, choose **Load Demo Data** at `https://wasp.hydroclimatex.com`.
The equivalent API request is:

```bash
curl --fail --show-error \
  -F "file=@examples/demo.csv" \
  -F "wavelet=db4" \
  -F "level=0" \
  -F "test_size=0.2" \
  -F "target_column=streamflow_anomaly" \
  -F "predictor_columns=sst_index" \
  -F "predictor_columns=soi" \
  -F "model=linear" \
  https://wasp.hydroclimatex.com/api/wasp/predict
```

Check service readiness with
`https://wasp.hydroclimatex.com/api/health`. A fresh Ubuntu 24.04 host is
prepared by `scripts/bootstrap-hk-server.sh`; subsequent guarded application
updates use `deploy.sh`.
