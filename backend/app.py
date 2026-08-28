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
from datetime import datetime, timezone
from typing import List, Literal, Optional
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import BackgroundTasks, FastAPI, File, UploadFile, Form, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from wasp.prediction import run_wasp_prediction
from wasp.utils import get_demo_csv
from usage import UsageTracker, new_session_token

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
USAGE_TRACKER = UsageTracker.from_env()
USAGE_COOKIE = "hx_wasp_session"
USAGE_SESSION_SECONDS = 30 * 60
SOFTWARE_DOWNLOAD_IDS = {
    "r": uuid5(NAMESPACE_URL, "https://wasp.hydroclimatex.com/downloads/WASP.zip"),
    "python": uuid5(NAMESPACE_URL, "https://wasp.hydroclimatex.com/downloads/WASP_python.zip"),
    "matlab": uuid5(NAMESPACE_URL, "https://wasp.hydroclimatex.com/downloads/WASP_matlab.zip"),
}

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


class UsageDownload(BaseModel):
    run_id: UUID


class SoftwareDownload(BaseModel):
    software: Literal["r", "python", "matlab"]


def usage_context(request: Request) -> tuple[str, str, bool]:
    existing = request.cookies.get(USAGE_COOKIE)
    session_token = existing or new_session_token()
    client_ip = request.headers.get("X-Real-IP") or (request.client.host if request.client else None)
    return session_token, USAGE_TRACKER.country(client_ip), existing is None


def set_usage_cookie(response: Response, session_token: str) -> None:
    response.set_cookie(
        USAGE_COOKIE,
        session_token,
        max_age=USAGE_SESSION_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def queue_usage_event(
    background_tasks: BackgroundTasks,
    event_type: str,
    *,
    session_token: str,
    country_code: str,
    run_id: str | None,
) -> None:
    background_tasks.add_task(
        USAGE_TRACKER.emit,
        event_type,
        session_token=session_token,
        country_code=country_code,
        run_id=run_id,
        occurred_at=datetime.now(timezone.utc),
    )


@app.post("/api/usage/session", status_code=204)
async def start_usage_session(request: Request, background_tasks: BackgroundTasks) -> Response:
    session_token, country_code, is_new = usage_context(request)
    if is_new:
        queue_usage_event(
            background_tasks, "session_start",
            session_token=session_token, country_code=country_code, run_id=None,
        )
    response = Response(status_code=204, background=background_tasks)
    set_usage_cookie(response, session_token)
    return response


@app.post("/api/usage/download", status_code=202)
async def record_usage_download(
    payload: UsageDownload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    session_token, country_code, is_new = usage_context(request)
    if is_new:
        queue_usage_event(
            background_tasks, "session_start",
            session_token=session_token, country_code=country_code, run_id=None,
        )
    queue_usage_event(
        background_tasks, "download",
        session_token=session_token, country_code=country_code, run_id=str(payload.run_id),
    )
    response = JSONResponse(status_code=202, content={"accepted": True}, background=background_tasks)
    set_usage_cookie(response, session_token)
    return response


@app.post("/api/usage/software-download", status_code=202)
async def record_software_download(
    payload: SoftwareDownload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    session_token, country_code, is_new = usage_context(request)
    if is_new:
        queue_usage_event(
            background_tasks, "session_start",
            session_token=session_token, country_code=country_code, run_id=None,
        )
    queue_usage_event(
        background_tasks, "download",
        session_token=session_token, country_code=country_code,
        run_id=str(SOFTWARE_DOWNLOAD_IDS[payload.software]),
    )
    response = JSONResponse(status_code=202, content={"accepted": True}, background=background_tasks)
    set_usage_cookie(response, session_token)
    return response

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
    request: Request,
    background_tasks: BackgroundTasks,
    response: Response,
    file: UploadFile = File(..., description="CSV containing selectable predictand and predictor columns"),
    wavelet: str = Form("db16", description="Daubechies wavelet (db1, db2, db4, db8, db16)"),
    level: int = Form(0, description="Decomposition level (0 = auto)"),
    test_size: float = Form(0.2, ge=0.1, le=0.5, description="Fraction of data for testing"),
    model: str = Form("linear", description="Regression model (linear, knn, xgboost)"),
    target_column: Optional[str] = Form(None, description="Predictand column; defaults to the first column"),
    predictor_columns: Optional[List[str]] = Form(None, description="Repeated predictor column names"),
):
    """
    Run the full WASP prediction pipeline.

    Upload a CSV file and optionally select one predictand plus one or more
    predictors by column name. The first column and all remaining columns are
    used when the selection fields are omitted.

    The pipeline will:
    1. Decompose each predictor using wavelet transform
    2. Identify predictive frequency bands via correlation
    3. Modulate variance to amplify signal, attenuate noise
    4. Reconstruct spectrally refined predictors
    5. Fit the selected regression model and evaluate

    Returns metrics comparing **WASP** vs **baseline** (raw predictors).
    """
    session_token, country_code, is_new_session = usage_context(request)
    run_id = str(uuid4())
    set_usage_cookie(response, session_token)
    if is_new_session:
        queue_usage_event(
            background_tasks, "session_start",
            session_token=session_token, country_code=country_code, run_id=None,
        )
    contents = await file.read()

    if len(contents) == 0:
        queue_usage_event(
            background_tasks, "run_failure",
            session_token=session_token, country_code=country_code, run_id=run_id,
        )
        error_response = JSONResponse(
            status_code=400,
            content={"success": False, "message": "Uploaded file is empty."},
            background=background_tasks,
        )
        set_usage_cookie(error_response, session_token)
        return error_response

    if len(contents) > MAX_UPLOAD_BYTES:
        queue_usage_event(
            background_tasks, "run_failure",
            session_token=session_token, country_code=country_code, run_id=run_id,
        )
        error_response = JSONResponse(
            status_code=413,
            content={
                "success": False,
                "message": f"File too large (max {MAX_UPLOAD_MB} MB).",
            },
            background=background_tasks,
        )
        set_usage_cookie(error_response, session_token)
        return error_response

    try:
        async with PREDICTION_SEMAPHORE:
            result = await run_in_threadpool(
                run_wasp_prediction,
                contents=contents,
                filename=file.filename or "upload.csv",
                wavelet=wavelet,
                level=level if level > 0 else None,
                test_size=test_size,
                model=model,
                target_column=target_column,
                predictor_columns=predictor_columns,
            )
    except (TypeError, ValueError) as error:
        queue_usage_event(
            background_tasks, "run_failure",
            session_token=session_token, country_code=country_code, run_id=run_id,
        )
        error_response = JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": f"Invalid WASP input: {error}",
            },
            background=background_tasks,
        )
        set_usage_cookie(error_response, session_token)
        return error_response
    except Exception:
        USAGE_TRACKER.emit(
            "run_failure",
            session_token=session_token,
            country_code=country_code,
            run_id=run_id,
        )
        raise

    if result.get('success'):
        result = dict(result)
        result["analytics_run_id"] = run_id
        queue_usage_event(
            background_tasks, "run_success",
            session_token=session_token, country_code=country_code, run_id=run_id,
        )
        return result
    else:
        queue_usage_event(
            background_tasks, "run_failure",
            session_token=session_token, country_code=country_code, run_id=run_id,
        )
        error_response = JSONResponse(
            status_code=400,
            content=result,
            background=background_tasks,
        )
        set_usage_cookie(error_response, session_token)
        return error_response


@app.get("/api/demo-data")
async def get_demo_data(name: str = "demo_q"):
    """Download a bundled demo CSV dataset for testing WASP-Web."""
    csv_bytes = get_demo_csv(name)
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={name}.csv"
        },
    )


# ---- Run ----
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, workers=1)
