# HydroclimateX Lab — Glossary

## Research Terms

**WASP (WAvelet System Prediction)**
A wavelet-based spectral transformation method that refines predictor representation for improved hydrologic prediction. Core innovation: modulating variance in predictors using wavelet theory before feeding into predictive models. Published in *Water Resources Research* (Jiang, Sharma & Johnson, 2020).

**WQM (Wavelet-based Quantile Mapping)**
Frequency-domain quantile mapping for post-processing numerical weather predictions. Corrects systematic bias in both trend and variability. Published in *Monthly Weather Review* (Jiang & Johnson, 2023).

**NPRED (Nonparametric PREDiction)**
A predictor identification tool that selects optimal predictors without assuming linear relationships.

**Spectral Transformation**
The core WASP technique: decomposing climate predictors into frequency components via wavelet transform, selectively modulating variance, then reconstructing — producing predictors more strongly linked to the predictand.

**Extreme Hydrometeorological Events**
Heavy precipitation, drought, floods, and compound events studied at seasonal to decadal timescales.

## System Terms

**GitHub Pages**
Static site hosting. Serves the academic profile pages (About, Research, Publications, Team) and the Showcase page that embeds the interactive WASP-Web demo.

**Alibaba Cloud ECS (Elastic Compute Service)**
Virtual machine running the FastAPI backend + WASP computation engine. Provisioned via the deploy script.

**Docker Compose**
Orchestrates two containers: (1) FastAPI + WASP Python, (2) Nginx reverse proxy with SSL.

**WASP-Web Frontend**
The interactive browser UI embedded in the Showcase page. Allows CSV upload, parameter selection, WASP computation trigger, and result visualization — all rendered in the browser talking to the FastAPI backend.

**deploy.sh**
One-click deployment script. On a fresh Alibaba Cloud ECS instance, running `bash deploy.sh` will: install Docker, pull images, start services, configure nginx, and obtain SSL cert.

**GitHub Actions Pages deployment**
The CI pipeline (`.github/workflows/static.yml`) that publishes the site to GitHub Pages. Runs on push to `main` (deploy), a daily schedule (refresh Scholar data + deploy), and manual dispatch. Requires Pages source = "GitHub Actions". See ADR-003.

**Scholar sync pipeline**
`scripts/sync_scholar.py` fetches the public Google Scholar profile and writes `data/scholar-stats.json` (citation metrics) and `data/scholar-publications.json` (full publication list), both committed to `main`. The site reads these JSON files at runtime; `main.js` falls back to curated data if they are unavailable.

**Web-root artifact (`_site`)**
The staged publish folder containing only the files the site serves (`index.html`, `main.js`, `style.css`, `assets/`, `showcase/`, `data/`). Everything else in the repo (backend, WASP-devel, docs, deploy scripts) is excluded from the deployed Pages site.
