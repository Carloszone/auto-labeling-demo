"""FastAPI entry point for the runnable auto-labeling demo."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

import av
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.defaults import (
    DATA_CHECK_DEFAULTS,
    DEFAULT_INPUT_PROMPT,
    EVENT_GENERATION_DEFAULTS,
    EVENT_LABELING_DEFAULTS,
    PARSER_DEFAULTS,
    copy_defaults,
)
from backend.errors import ApiError
from backend.job_service import JobService
from backend.logging_config import configure_logging
from backend.schemas import EventPatch, RunRequest
from backend.settings import settings


configure_logging(settings)
app = FastAPI(title="Auto Labeling Demo", version="0.1.0")
jobs = JobService(settings)
LOGGER = logging.getLogger(__name__)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    started = time.perf_counter()
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        LOGGER.exception(
            "http_request request_id=%s method=%s path=%s status=500 duration_sec=%.6f",
            request_id, request.method, request.url.path, time.perf_counter() - started,
        )
        raise
    response.headers["X-Request-ID"] = request_id
    LOGGER.info(
        "http_request request_id=%s method=%s path=%s status=%s duration_sec=%.6f",
        request_id, request.method, request.url.path, response.status_code,
        time.perf_counter() - started,
    )
    return response


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {
            "code": exc.code, "message": exc.message, "details": exc.details,
            "request_id": getattr(request.state, "request_id", ""),
        }},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {
            "code": "CONFIG_VALIDATION_FAILED", "message": "请求参数校验失败",
            "details": {
                "errors": jsonable_encoder(exc.errors(), custom_encoder={Exception: str})
            },
            "request_id": getattr(request.state, "request_id", ""),
        }},
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    LOGGER.exception("Unhandled API error request_id=%s", getattr(request.state, "request_id", ""))
    return JSONResponse(
        status_code=500,
        content={"error": {
            "code": "INTERNAL_ERROR", "message": "服务内部错误",
            "details": {}, "request_id": getattr(request.state, "request_id", ""),
        }},
    )


@app.get("/api/v1/health")
async def health() -> dict:
    return {
        "status": "ok", "version": app.version,
        "vlm_configured": bool(settings.vlm_endpoint),
        "workspace_writable": settings.workspace_root.exists() and settings.workspace_root.is_dir(),
        "h264_encoder_available": "libx264" in av.codec.codecs_available,
    }


@app.get("/api/v1/config")
async def config() -> dict:
    return {
        "max_upload_bytes": settings.max_upload_bytes,
        "fps": 30,
        "default_input_prompt": DEFAULT_INPUT_PROMPT,
        "pipeline_defaults": {
            "parser_config": copy_defaults(PARSER_DEFAULTS),
            "data_check_config": copy_defaults(DATA_CHECK_DEFAULTS),
            "event_generation_config": copy_defaults(EVENT_GENERATION_DEFAULTS),
            "event_labeling_config": copy_defaults(EVENT_LABELING_DEFAULTS),
        },
        "capabilities": {"multi_mcap": False, "cancel": False, "timeline_zoom": False},
    }


@app.post("/api/v1/jobs", status_code=201)
async def create_job(
    mcap: UploadFile = File(...), robot_config: UploadFile = File(...)
) -> dict:
    return await jobs.create_job(mcap, robot_config)


@app.get("/api/v1/jobs/current")
async def current_job() -> dict:
    return jobs.current_summary()


@app.get("/api/v1/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    return jobs.summary(job_id)


@app.post("/api/v1/jobs/{job_id}/run", status_code=202)
async def run_job(job_id: str, request: RunRequest) -> dict:
    return jobs.start(job_id, request)


@app.get("/api/v1/jobs/{job_id}/result")
async def get_result(job_id: str) -> dict:
    return jobs.result(job_id)


@app.patch("/api/v1/jobs/{job_id}/events/{event_id}")
async def update_event(job_id: str, event_id: str, patch: EventPatch) -> dict:
    return jobs.update_event(job_id, event_id, patch)


@app.get("/api/v1/jobs/{job_id}/videos/{camera_key}")
async def get_video(job_id: str, camera_key: str) -> FileResponse:
    return FileResponse(jobs.video_path(job_id, camera_key), media_type="video/mp4")


@app.get("/api/v1/jobs/{job_id}/export")
async def export(job_id: str) -> FileResponse:
    path = jobs.export_path(job_id)
    return FileResponse(path, media_type="application/json", filename=jobs.export_filename(job_id))


@app.delete("/api/v1/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str) -> None:
    jobs.delete(job_id)


dist = settings.frontend_dist.resolve()
if dist.exists():
    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend(full_path: str):
        requested = (dist / full_path).resolve()
        if full_path and requested.is_relative_to(dist) and requested.is_file():
            return FileResponse(requested)
        # Serving the small SPA shell directly also keeps in-process ASGI smoke
        # tests independent of AnyIO's file-response worker thread.
        return HTMLResponse((dist / "index.html").read_text())


def run() -> None:
    import uvicorn

    uvicorn.run("backend.main:app", host=settings.host, port=settings.port, workers=1)


if __name__ == "__main__":
    run()
