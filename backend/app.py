"""
HydroclimateX Lab — FastAPI Backend for WASP-Web
================================================
REST API for the WASP interactive application.
Nginx serves the application and proxies its same-origin /api requests.

Author: HydroclimateX Lab
Date: 2026-08-01

Endpoints:
  GET  /api/health        — API health check
  POST /api/wasp/predict  — Run WASP prediction pipeline
  GET  /api/demo-data     — Download demo CSV dataset
"""

import asyncio
import io
import os

from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from wasp.prediction import run_wasp_prediction
from wasp.utils import make_demo_csv

# ---- App Setup ----
app = FastAPI(
    title="WASP-Web API",
    description="WAvelet System Prediction — API for HydroclimateX Lab",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

MAX_UPLOAD_MB = int(os.getenv("WASP_MAX_UPLOAD_MB", "10"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
PREDICTION_SEMAPHORE = asyncio.Semaphore(1)

# CORS is needed only for the Pages introduction and explicit local development.
# NOTE: Starlette does not support port wildcards like "http://localhost:*",
# so localhost is allowed via a regex covering any port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://hydroclimatex.com",
        "https://www.hydroclimatex.com",
        "https://wasp.hydroclimatex.com",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
)


# ---- Endpoints ----

@app.get("/api/health")
async def health():
    """Return the public health and API-discovery response."""
    return {
        "service": "WASP-Web API",
        "version": "1.0.0",
        "status": "healthy",
        "docs": "/api/docs",
        "endpoints": {
            "predict": "POST /api/wasp/predict",
            "demo_data": "GET /api/demo-data",
        },
    }


@app.post("/api/wasp/predict")
async def predict(
    file: UploadFile = File(..., description="CSV file: 1st column = predictand, remaining = predictors"),
    wavelet: str = Form("db4", description="Wavelet family (db4, sym8, coif3, haar)"),
    level: int = Form(0, description="Decomposition level (0 = auto)"),
    test_size: float = Form(0.2, ge=0.1, le=0.5, description="Fraction of data for testing"),
    alpha: float = Form(1.0, ge=0.01, le=100.0, description="Ridge regularization strength"),
):
    """
    Run the full WASP prediction pipeline.

    Upload a CSV file where:
    - **First column** = predictand (target variable, e.g., streamflow anomaly)
    - **Remaining columns** = predictors (e.g., SST indices, climate variables)

    The pipeline will:
    1. Decompose each predictor using wavelet transform
    2. Identify predictive frequency bands via correlation
    3. Modulate variance to amplify signal, attenuate noise
    4. Reconstruct spectrally refined predictors
    5. Fit Ridge regression model and evaluate

    Returns metrics comparing **WASP** vs **baseline** (raw predictors).
    """
    contents = await file.read()

    if len(contents) == 0:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Uploaded file is empty."}
        )

    if len(contents) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            status_code=413,
            content={
                "success": False,
                "message": f"File too large (max {MAX_UPLOAD_MB} MiB).",
            },
        )

    try:
        async with PREDICTION_SEMAPHORE:
            result = await run_in_threadpool(
                run_wasp_prediction,
                contents=contents,
                filename=file.filename or "upload.csv",
                wavelet=wavelet,
                level=level if level > 0 else None,
                test_size=test_size,
                alpha=alpha,
            )
    except (TypeError, ValueError) as error:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": f"Invalid WASP input: {error}",
            },
        )

    if result.get('success'):
        return result
    else:
        return JSONResponse(status_code=400, content=result)


@app.get("/api/demo-data")
async def get_demo_data():
    """Download the demo CSV dataset for testing WASP-Web."""
    csv_bytes = make_demo_csv()
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=wasp_demo.csv"
        },
    )


# ---- Run ----
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, workers=1)
