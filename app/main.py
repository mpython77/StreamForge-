from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pathlib import Path
import subprocess
import logging
import logging.handlers
import sys
import platform

from .routes import router
from .config import config
from .middleware import limiter, rate_limit_handler, api_key_middleware
from .metrics import metrics_middleware, metrics_endpoint

# ═══════════════════════════════════════════════════════════
# LOGGING — File + Console with rotation
# ═══════════════════════════════════════════════════════════

LOG_DIR = Path("./logs")
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("streamforge")
logger.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S"
))
logger.addHandler(console_handler)

# File handler with rotation (10MB max, keep 5 backups)
file_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "streamforge.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)-7s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
))
logger.addHandler(file_handler)


# ═══════════════════════════════════════════════════════════
# APPLICATION
# ═══════════════════════════════════════════════════════════

app = FastAPI(
    title="StreamForge",
    description="Pro-Grade Video Processing & HLS Streaming Framework",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ─── Middleware Stack (order matters: last added = first executed) ────
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(429, rate_limit_handler)

# API key auth (optional — only active when API_KEY is set in .env)
app.middleware("http")(api_key_middleware)

# Prometheus metrics middleware
app.middleware("http")(metrics_middleware)

# ─── Routes ──────────────────────────────────────────────────
app.include_router(router)

# Prometheus metrics endpoint
app.get("/metrics", include_in_schema=False)(metrics_endpoint)

# Ensure required directories exist
for d in ["uploads", "output", "static", "logs"]:
    Path(f"./{d}").mkdir(exist_ok=True)

# Static files
app.mount("/output", StaticFiles(directory="output"), name="output")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ═══════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_checks():
    """Verify critical dependencies and log system info"""
    logger.info("=" * 60)
    logger.info("StreamForge v1.0.0 starting...")
    logger.info(f"Platform: {platform.system()} {platform.release()} ({platform.machine()})")
    logger.info(f"Python: {sys.version.split()[0]}")

    # Check FFmpeg
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            logger.error("⚠ FFmpeg returned error. Video processing will fail.")
        else:
            version = r.stdout.split("\n")[0] if r.stdout else "unknown"
            logger.info(f"✓ FFmpeg: {version}")
    except FileNotFoundError:
        logger.error("✗ FFmpeg NOT FOUND! Install: https://ffmpeg.org/download.html")
    except Exception as e:
        logger.warning(f"⚠ FFmpeg check failed: {e}")

    # Check FFprobe
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, text=True, timeout=5)
        logger.info("✓ FFprobe available")
    except FileNotFoundError:
        logger.error("✗ FFprobe NOT FOUND!")
    except Exception:
        pass

    # Feature flags
    features = []
    if config.has_api_key():
        features.append("API-Key-Auth")
    features.append(f"RateLimit({config.RATE_LIMIT})")
    if config.has_r2():
        features.append("R2-Storage")
    if config.has_webhook():
        features.append("Webhooks")
    features.append("Prometheus")

    logger.info(f"Features: {', '.join(features)}")
    logger.info(f"Logs: {LOG_DIR.resolve()}/streamforge.log")
    logger.info(f"Docs: http://localhost:{config.PORT}/docs")
    logger.info(f"Metrics: http://localhost:{config.PORT}/metrics")
    logger.info(f"Ready: http://localhost:{config.PORT}")
    logger.info("=" * 60)


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    from .routes import jobs
    active = sum(1 for j in jobs.values() if j.get("status") == "processing")
    return {
        "status": "ok",
        "version": "1.0.0",
        "active_jobs": active,
        "features": {
            "api_key": config.has_api_key(),
            "r2": config.has_r2(),
            "webhook": config.has_webhook(),
        },
    }
