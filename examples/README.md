# WASP example data

The research-team homepage is `https://hydroclimatex.com`; methodology and file
requirements are documented at
`https://hydroclimatex.com/showcase/wasp-web/`; the interactive application runs
at `https://wasp.hydroclimatex.com` on the Alibaba Cloud Hong Kong Lightweight
Application Server.

## `demo.csv`

This file contains 391 monthly observations. The first column is the target and
the remaining four columns are predictors.

| Column | Role |
|---|---|
| `streamflow_anomaly` | Target: standardised streamflow anomaly |
| `sst_index` | Sea-surface-temperature predictor |
| `soi` | Southern Oscillation Index predictor |
| `pdo_index` | Pacific Decadal Oscillation predictor |
| `precip_index` | Precipitation predictor |

All input columns must be numeric, finite, and non-constant. Files must contain
30–5000 rows and 1–50 predictors. Nginx accepts an 11 MB multipart request, and
FastAPI enforces a 10 MiB limit on the uploaded file itself. Supported wavelets
are `db4`, `sym8`, `coif3`, and `haar`.

## Use the example

In the browser, choose **Load Demo Data** at `https://wasp.hydroclimatex.com`.
The equivalent API request is:

```bash
curl --fail --show-error \
  -F "file=@examples/demo.csv" \
  -F "wavelet=db4" \
  -F "level=0" \
  -F "test_size=0.2" \
  -F "alpha=1.0" \
  https://wasp.hydroclimatex.com/api/wasp/predict
```

Check service readiness with
`https://wasp.hydroclimatex.com/api/health`. A fresh Ubuntu 24.04 host is
prepared by `scripts/bootstrap-hk-server.sh`; subsequent guarded application
updates use `deploy.sh`.
