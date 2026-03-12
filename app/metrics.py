# ═══════════════════════════════════════════════════════════
# StreamForge — Prometheus Metrics
# ═══════════════════════════════════════════════════════════

import time
import logging
from fastapi import Request
from prometheus_client import (
    Counter, Histogram, Gauge, Info,
    generate_latest, CONTENT_TYPE_LATEST,
)
from fastapi.responses import Response

logger = logging.getLogger("streamforge")

# ─── Metrics Definitions ────────────────────────────────────

# Application info
app_info = Info("streamforge", "StreamForge application info")
app_info.info({"version": "1.0.0", "framework": "fastapi"})

# HTTP metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)
http_request_duration = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

# Video processing metrics
videos_uploaded = Counter("videos_uploaded_total", "Total videos uploaded")
videos_processed = Counter(
    "videos_processed_total",
    "Total videos processed",
    ["status"],  # completed, error, cancelled
)
processing_duration = Histogram(
    "video_processing_duration_seconds",
    "Video processing duration in seconds",
    buckets=[10, 30, 60, 120, 300, 600, 1800, 3600],
)
active_jobs = Gauge("active_jobs", "Number of currently processing jobs")
upload_size_bytes = Histogram(
    "upload_size_bytes",
    "Upload file sizes in bytes",
    buckets=[1e6, 10e6, 50e6, 100e6, 500e6, 1e9, 2e9],
)

# R2 metrics
r2_uploads = Counter("r2_uploads_total", "Total R2 uploads", ["status"])


# ─── Metrics Middleware ──────────────────────────────────────

async def metrics_middleware(request: Request, call_next):
    """Track HTTP request metrics"""
    # Skip metrics for the metrics endpoint itself
    if request.url.path == "/metrics":
        return await call_next(request)

    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    # Normalize endpoint (collapse IDs into {id})
    endpoint = request.url.path
    for prefix in ("/api/status/", "/api/probe/", "/api/videos/",
                   "/api/download/", "/api/cancel/", "/api/r2/upload/",
                   "/api/r2/upload-status/", "/ws/status/"):
        if endpoint.startswith(prefix):
            endpoint = prefix + "{id}"
            break

    http_requests_total.labels(
        method=request.method,
        endpoint=endpoint,
        status_code=response.status_code,
    ).inc()

    http_request_duration.labels(
        method=request.method,
        endpoint=endpoint,
    ).observe(duration)

    return response


# ─── Metrics Endpoint ────────────────────────────────────────

async def metrics_endpoint():
    """Expose Prometheus metrics"""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
