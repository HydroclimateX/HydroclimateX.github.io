# HydroclimateX Lab — Glossary

## Research terms

**WASP (WAvelet System Prediction)**
A spectral-transformation method that decomposes predictors, identifies useful
frequency bands, modulates their variance, and reconstructs refined predictors
for hydrologic prediction.

**Predictand / target**
The selected predictand (Y) in a WASP CSV file: the quantity the model predicts.
The browser selects the first column by default, and the user can change it.

**Predictor**
A selected input column (X) used to predict Y. At least one numeric, finite,
non-constant predictor is required; unselected columns are ignored.

**Prediction model**
The estimator fitted to both the WASP-refined and raw baseline predictors.
The web application offers Linear Regression, K-Nearest Neighbors, and XGBoost.

**WQM (Wavelet-based Quantile Mapping)**
Frequency-domain quantile mapping for correcting systematic bias in numerical
weather and climate simulations.

**NPRED (Nonparametric PREDiction)**
A predictor-identification method that does not assume a linear relationship.

## System terms

**Research-team homepage**
`https://hydroclimatex.com`, published by GitHub Pages.

**WASP scientific introduction**
`https://hydroclimatex.com/showcase/wasp-web/`, a static Pages document covering
the method, publications, software implementations, and input contract.

**WASP application**
`https://wasp.hydroclimatex.com`, served from the Alibaba Cloud Hong Kong
Lightweight Application Server. Its browser code and API share the same origin.

**Same-origin API**
The application calls `/api/health`, `/api/demo-data`, and
`/api/wasp/predict`. Nginx forwards these paths to FastAPI without exposing
container port 8000 on the host.

**Docker Compose**
Defines the two long-lived services: `wasp-nginx` for static files, TLS, and
proxying; and `wasp-api` for computation. Certbot runs only as an on-demand task.

**`scripts/bootstrap-hk-server.sh`**
The host bootstrap. It installs Ubuntu dependencies and Docker, creates the
persistent directories, synchronises the requested Git branch, and calls the
deployment guard.

**`deploy.sh`**
The application deployment guard. It validates DNS, coordinates the restricted
ACME configuration and final TLS configuration, waits for health checks,
verifies the HTTPS API, and installs certificate renewal.

**GitHub Pages artifact**
The `_site` directory created by `.github/workflows/static.yml`. It contains the
homepage, scientific introduction, figures, and public data, but not the actual
WASP application, backend, certificates, or deployment scripts.
