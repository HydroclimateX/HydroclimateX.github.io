# ADR-001: System Architecture

**Date:** 2026-08-01
**Status:** Accepted (revised)

## Context

HydroclimateX Lab needs a web presence with:
1. Static academic profile: About, Research, Publications, Showcase, Contact
2. Interactive WASP-Web demo embedded under Showcase

## Decision

```
HydroclimateX/                      # Project root
├── index.html                      # Main site (About, Research, Publications, Showcase, Contact)
├── style.css / main.js             # Static assets
├── assets/                         # Images & figures
├── showcase/
│   └── wasp-web/                   # WASP-Web interactive demo (from WASP-devel)
├── backend/                        # FastAPI + WASP computation engine
│   ├── app.py
│   ├── wasp/
│   └── static/demo.html
├── docker-compose.yml              # FastAPI + Nginx
├── deploy.sh                       # One-click Alibaba Cloud deploy
├── examples/                       # Demo datasets
└── docs/                           # ADRs, glossary
```

### Key boundaries
- **GitHub Pages**: `index.html` + `style.css` + `main.js` + `assets/` — full static site.
  Showcase page embeds WASP-Web via iframe (`showcase/wasp-web/`).
- **Alibaba Cloud ECS**: Runs Docker Compose with FastAPI backend for on-demand
  WASP computation (optional — the showcase demo runs client-side via WebR).
- **Showcase**: Only WASP-Web. The demo (`hydro_model_improved.html`) runs in-browser
  using WebR (R-in-browser), so no backend is strictly required for the demo.

### Pages
| Page | Content |
|------|---------|
| About | Lab identity: "Hydroclimate Extremes Modelling & Forecasting". Extreme flood/drought illustration. |
| Research | 5 research interests from HHU profile |
| Publications | Featured papers + full list, Google Scholar sync |
| Showcase | WASP-Web interactive demo only |
| Contact | Email, address, GitHub, Scholar, ORCID, recruitment |

## Rationale

- WASP-Web is a showcase example — not the main project identity
- HydroclimateX Lab is the brand; WASP is one of its software products
- Flat structure: easier to deploy to GitHub Pages (just push root)
- Showcase as subfolder keeps demo isolated but accessible via relative paths

## Consequences

- No cross-origin issues for the demo (served from same origin via GitHub Pages)
- For heavy computation, the FastAPI backend on Alibaba Cloud handles `/api/wasp/predict`
- Demo page uses WebR for client-side WASP, so it works even without the backend running
