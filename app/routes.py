from fastapi import APIRouter, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, field_validator
from typing import Optional
from dataclasses import asdict
import uuid
import os
import re
import shutil
import asyncio
import zipfile
import io
import time as _time
import logging
from pathlib import Path

from .processor import VideoProcessor, ENCODING_PRESETS, QualityProfile
from .hardware import detect_hardware
from .config import config
from .webhook import send_webhook

logger = logging.getLogger("streamforge")

router = APIRouter(prefix="/api")
processor = VideoProcessor(upload_dir="./uploads", output_dir="./output")

# In-memory storage
jobs: dict = {}
_hardware_cache: dict | None = None

# Constants
MAX_UPLOAD_SIZE = config.MAX_UPLOAD_SIZE
MAX_JOBS = config.MAX_JOBS
JOB_TTL_SECONDS = config.JOB_TTL_SECONDS

# Active FFmpeg processes for cancellation
_active_processes: dict = {}


# ═══════════════════════════════════════════════════════════
# SECURITY HELPERS
# ═══════════════════════════════════════════════════════════

def _validate_id(value: str) -> str:
    """Validate an ID is safe (alphanumeric + hyphens only) — prevents path traversal"""
    if not re.match(r'^[a-zA-Z0-9_-]+$', value):
        raise HTTPException(400, "Invalid ID: only alphanumeric, hyphens, and underscores allowed")
    return value


def _cleanup_old_jobs():
    """Remove old jobs to prevent memory leak"""
    now = _time.time()
    expired = [jid for jid, j in jobs.items()
               if j.get("started_at", now) < now - JOB_TTL_SECONDS
               and j.get("status") in ("completed", "error")]
    for jid in expired:
        del jobs[jid]

    # Hard limit
    if len(jobs) > MAX_JOBS:
        completed = sorted(
            [(jid, j) for jid, j in jobs.items() if j.get("status") in ("completed", "error")],
            key=lambda x: x[1].get("started_at", 0)
        )
        for jid, _ in completed[:len(jobs) - MAX_JOBS]:
            del jobs[jid]


# ═══════════════════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════════════════

class ProcessRequest(BaseModel):
    video_id: str
    qualities: list[str] = ["720p", "480p", "360p"]
    segment_duration: int = 4
    generate_thumbnail: bool = True
    encoder: str = "libx264"
    threads: int = 0
    parallel: bool = False
    max_parallel: int = 2
    encoding_preset: str = "balanced"
    trim_start: float = 0
    trim_end: float = 0
    audio_bitrate: str = ""
    audio_normalize: bool = False
    encrypt: bool = False
    extract_subs: bool = True
    generate_sprites: bool = True

    @field_validator('segment_duration')
    @classmethod
    def validate_segment_duration(cls, v):
        if v < 1 or v > 30:
            raise ValueError('segment_duration must be between 1 and 30')
        return v

    @field_validator('encoding_preset')
    @classmethod
    def validate_encoding_preset(cls, v):
        if v not in ENCODING_PRESETS:
            raise ValueError(f'Unknown preset: {v}. Available: {list(ENCODING_PRESETS.keys())}')
        return v

    @field_validator('qualities')
    @classmethod
    def validate_qualities(cls, v):
        valid = set(QualityProfile.ALL_PRESETS.keys())
        invalid = [q for q in v if q not in valid]
        if invalid:
            raise ValueError(f'Unknown qualities: {invalid}. Available: {sorted(valid)}')
        if not v:
            raise ValueError('At least one quality is required')
        return v

    @field_validator('max_parallel')
    @classmethod
    def validate_max_parallel(cls, v):
        if v < 1 or v > 8:
            raise ValueError('max_parallel must be between 1 and 8')
        return v


# ═══════════════════════════════════════════════════════════
# HARDWARE
# ═══════════════════════════════════════════════════════════

@router.get("/hardware")
async def get_hardware():
    """Detect hardware capabilities"""
    global _hardware_cache
    if _hardware_cache is None:
        hw = detect_hardware()
        _hardware_cache = asdict(hw)
    return _hardware_cache


@router.post("/hardware/refresh")
async def refresh_hardware():
    """Refresh hardware detection"""
    global _hardware_cache
    hw = detect_hardware()
    _hardware_cache = asdict(hw)
    return _hardware_cache


# ═══════════════════════════════════════════════════════════
# ENCODING PRESETS
# ═══════════════════════════════════════════════════════════

@router.get("/presets")
async def get_presets():
    """Get available encoding presets"""
    return ENCODING_PRESETS


# ═══════════════════════════════════════════════════════════
# UPLOAD
# ═══════════════════════════════════════════════════════════

@router.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    """Upload video file with streaming (chunked, memory-safe)"""
    allowed = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".3gp"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed:
        raise HTTPException(400, f"Unsupported format: {ext}")

    video_id = str(uuid.uuid4())[:8]
    upload_path = Path("./uploads") / f"{video_id}{ext}"
    upload_path.parent.mkdir(parents=True, exist_ok=True)

    # Streaming upload — reads in 1MB chunks (never loads whole file to RAM)
    total_written = 0
    chunk_size = 1024 * 1024  # 1MB
    try:
        with open(upload_path, "wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_UPLOAD_SIZE:
                    f.close()
                    os.remove(upload_path)
                    raise HTTPException(413, f"File too large. Max: {MAX_UPLOAD_SIZE // (1024*1024)}MB")
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        if upload_path.exists():
            os.remove(upload_path)
        raise HTTPException(500, f"Upload failed: {e}")

    try:
        info = processor.probe(str(upload_path))
    except ValueError as e:
        os.remove(upload_path)
        raise HTTPException(400, str(e))

    return {
        "video_id": video_id,
        "filename": file.filename,
        "file_path": str(upload_path),
        "info": info,
    }


# ═══════════════════════════════════════════════════════════
# PROBE
# ═══════════════════════════════════════════════════════════

@router.get("/probe/{video_id}")
async def probe_video(video_id: str):
    """Get video information"""
    _validate_id(video_id)
    upload_dir = Path("./uploads")
    matching = list(upload_dir.glob(f"{video_id}.*"))
    if not matching:
        raise HTTPException(404, "Video not found")

    try:
        info = processor.probe(str(matching[0]))
        return {"video_id": video_id, "info": info}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ═══════════════════════════════════════════════════════════
# ESTIMATE
# ═══════════════════════════════════════════════════════════

class EstimateRequest(BaseModel):
    video_id: str
    qualities: list[str] = ["720p", "480p", "360p"]
    encoding_preset: str = "balanced"
    encoder: str = "libx264"
    parallel: bool = False
    max_parallel: int = 2
    trim_start: float = 0
    trim_end: float = 0
    threads: int = 0


@router.post("/estimate")
async def estimate_time(request: EstimateRequest):
    """Calculate accurate time and size estimates"""
    upload_dir = Path("./uploads")
    matching = list(upload_dir.glob(f"{request.video_id}.*"))
    if not matching:
        raise HTTPException(404, "Video not found")

    try:
        result = processor.estimate(
            input_file=str(matching[0]),
            qualities=request.qualities,
            encoding_preset=request.encoding_preset,
            encoder=request.encoder,
            parallel=request.parallel,
            max_parallel=request.max_parallel,
            trim_start=request.trim_start,
            trim_end=request.trim_end,
            threads=request.threads,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


# ═══════════════════════════════════════════════════════════
# PROCESS
# ═══════════════════════════════════════════════════════════

@router.post("/process")
async def process_video(request: ProcessRequest):
    """Process video to HLS"""
    _validate_id(request.video_id)
    _cleanup_old_jobs()

    upload_dir = Path("./uploads")
    matching = list(upload_dir.glob(f"{request.video_id}.*"))
    if not matching:
        raise HTTPException(404, "Video not found")

    input_file = str(matching[0])
    job_id = str(uuid.uuid4())[:8]

    # Estimate
    estimate = None
    try:
        estimate = processor.estimate(
            input_file=input_file,
            qualities=request.qualities,
            encoding_preset=request.encoding_preset,
            encoder=request.encoder,
            parallel=request.parallel,
            max_parallel=request.max_parallel,
            trim_start=request.trim_start,
            trim_end=request.trim_end,
            threads=request.threads,
        )
    except Exception:
        pass

    started_at = _time.time()

    jobs[job_id] = {
        "job_id": job_id,
        "video_id": request.video_id,
        "status": "processing",
        "progress": {
            "step": 0, "total": len(request.qualities) + 2,
            "status": "Starting...", "percent": 0,
            "elapsed": 0,
            "eta": estimate["total_estimated_time_formatted"] if estimate else "—",
            "eta_seconds": estimate["total_estimated_time_seconds"] if estimate else 0,
        },
        "estimate": estimate,
        "started_at": started_at,
        "result": None,
        "error": None,
        "settings": {
            "encoder": request.encoder,
            "preset": request.encoding_preset,
            "parallel": request.parallel,
            "trim": [request.trim_start, request.trim_end],
            "audio_normalize": request.audio_normalize,
        },
    }

    asyncio.get_running_loop().run_in_executor(
        None, _process_in_background, job_id, input_file, request,
    )

    return {"job_id": job_id, "status": "processing", "estimate": estimate}


def _process_in_background(job_id: str, input_file: str, req: ProcessRequest):
    """Background video processing with full error logging"""
    def update_progress(progress: dict):
        if job_id in jobs:
            jobs[job_id]["progress"] = progress

    try:
        result = processor.process(
            input_file=input_file,
            video_id=req.video_id,
            qualities=req.qualities,
            segment_duration=req.segment_duration,
            generate_thumbnail=req.generate_thumbnail,
            progress_callback=update_progress,
            encoder=req.encoder,
            threads=req.threads,
            parallel=req.parallel,
            max_parallel=req.max_parallel,
            encoding_preset=req.encoding_preset,
            trim_start=req.trim_start,
            trim_end=req.trim_end,
            audio_bitrate=req.audio_bitrate,
            audio_normalize=req.audio_normalize,
            encrypt=req.encrypt,
            extract_subs=req.extract_subs,
            generate_sprites=req.generate_sprites,
        )
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["result"] = result
        send_webhook("job.completed", {
            "job_id": job_id,
            "video_id": req.video_id,
            "qualities": req.qualities,
            "stats": result.get("stats") if isinstance(result, dict) else None,
        })
    except Exception as e:
        logger.exception(f"Processing failed for job {job_id}: {e}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        send_webhook("job.error", {
            "job_id": job_id,
            "video_id": req.video_id,
            "error": str(e),
        })


# ═══════════════════════════════════════════════════════════
# STATUS & HISTORY
# ═══════════════════════════════════════════════════════════

@router.get("/status/{job_id}")
async def get_status(job_id: str):
    """Check processing status — real-time elapsed and ETA"""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    job = jobs[job_id]

    # Real-time elapsed and ETA calculation
    if job["status"] == "processing" and "started_at" in job:
        elapsed = _time.time() - job["started_at"]
        job["progress"]["elapsed"] = round(elapsed, 1)
        job["progress"]["elapsed_formatted"] = _fmt_time(elapsed)

        percent = job["progress"].get("percent", 0)
        if percent > 0:
            total_est = elapsed / (percent / 100)
            remaining = max(0, total_est - elapsed)
            job["progress"]["eta_seconds"] = round(remaining, 1)
            job["progress"]["eta"] = _fmt_time(remaining)
        elif job.get("estimate"):
            est = job["estimate"]["total_estimated_time_seconds"]
            remaining = max(0, est - elapsed)
            job["progress"]["eta_seconds"] = round(remaining, 1)
            job["progress"]["eta"] = _fmt_time(remaining)

    return job


def _fmt_time(s):
    s = int(s)
    if s >= 3600:
        return f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}"
    return f"{s//60:02d}:{s%60:02d}"


@router.get("/jobs")
async def list_jobs():
    """List all jobs"""
    return {
        "jobs": list(jobs.values()),
        "total": len(jobs),
    }


# ═══════════════════════════════════════════════════════════
# VIDEOS
# ═══════════════════════════════════════════════════════════

@router.get("/videos")
async def list_videos():
    """List all processed videos"""
    output_dir = Path("./output")
    if not output_dir.exists():
        return {"videos": []}

    videos = []
    for item in output_dir.iterdir():
        if item.is_dir():
            metadata_file = item / "metadata.json"
            if metadata_file.exists():
                import json
                with open(metadata_file, encoding="utf-8") as f:
                    metadata = json.load(f)
                # Add file size info
                size_info = processor.get_output_size(item.name)
                metadata["disk_usage"] = size_info
                videos.append(metadata)

    return {"videos": videos}


@router.delete("/videos/{video_id}")
async def delete_video(video_id: str):
    """Delete a video"""
    _validate_id(video_id)
    cleaned = processor.cleanup(video_id)
    if not cleaned:
        raise HTTPException(404, "Video not found")
    return {"status": "deleted", "video_id": video_id}


# ═══════════════════════════════════════════════════════════
# DOWNLOAD
# ═══════════════════════════════════════════════════════════

@router.get("/download/{video_id}")
async def download_video(video_id: str):
    """Download processed HLS files as ZIP"""
    output_path = Path("./output") / _validate_id(video_id)
    if not output_path.exists():
        raise HTTPException(404, "Video not found")

    zip_path = output_path / f"{video_id}_hls.zip"

    # Create ZIP archive
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
        for file in output_path.rglob("*"):
            if file.is_file() and file.suffix != ".zip":
                arcname = str(file.relative_to(output_path))
                zf.write(str(file), arcname)

    return FileResponse(
        str(zip_path),
        media_type="application/zip",
        filename=f"{video_id}_hls.zip",
    )


# ═══════════════════════════════════════════════════════════
# SYSTEM
# ═══════════════════════════════════════════════════════════

@router.get("/disk")
async def disk_usage():
    """Get disk usage"""
    upload_size = sum(f.stat().st_size for f in Path("./uploads").rglob("*") if f.is_file()) if Path("./uploads").exists() else 0
    output_size = sum(f.stat().st_size for f in Path("./output").rglob("*") if f.is_file()) if Path("./output").exists() else 0

    return {
        "uploads_mb": round(upload_size / (1024 * 1024), 2),
        "outputs_mb": round(output_size / (1024 * 1024), 2),
        "total_mb": round((upload_size + output_size) / (1024 * 1024), 2),
    }


@router.post("/cleanup")
async def cleanup_all():
    """Clean up all files"""
    stats = processor.cleanup_all()
    return {"status": "cleaned", "stats": stats}


# ═══════════════════════════════════════════════════════════
# CLOUDFLARE R2 STORAGE
# ═══════════════════════════════════════════════════════════

from .storage import storage


class R2ConfigRequest(BaseModel):
    account_id: str
    access_key: str
    secret_key: str
    bucket: str
    public_url: str = ""


@router.post("/r2/configure")
async def r2_configure(config: R2ConfigRequest):
    """Configure R2 storage"""
    try:
        storage.configure(
            account_id=config.account_id,
            access_key=config.access_key,
            secret_key=config.secret_key,
            bucket=config.bucket,
            public_url=config.public_url,
        )
        test = storage.test_connection()
        return {"status": "configured", "test": test}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/r2/status")
async def r2_status():
    """Get R2 status"""
    if not storage.configured:
        return {"configured": False}
    test = storage.test_connection()
    return {"configured": True, "test": test, "bucket": storage.bucket}


@router.post("/r2/upload/{video_id}")
async def r2_upload(video_id: str):
    """Upload video output to R2"""
    if not storage.configured:
        raise HTTPException(400, "R2 not configured. Call /api/r2/configure first")

    _validate_id(video_id)
    output_dir = f"./output/{video_id}"
    if not os.path.exists(output_dir):
        raise HTTPException(404, f"Output not found: {video_id}")

    # Upload key
    upload_key = f"r2_upload_{video_id}"
    jobs[upload_key] = {
        "job_id": upload_key,
        "video_id": video_id,
        "status": "uploading",
        "progress": {"uploaded": 0, "total": 0, "current_file": "", "percent": 0},
        "result": None,
        "error": None,
    }

    def do_upload():
        def on_progress(uploaded, total, filename):
            if upload_key in jobs:
                jobs[upload_key]["progress"] = {
                    "uploaded": uploaded,
                    "total": total,
                    "current_file": filename,
                    "percent": int((uploaded / total) * 100) if total > 0 else 0,
                }
        try:
            result = storage.upload_directory(
                local_dir=output_dir,
                remote_prefix=f"videos/{video_id}",
                progress_callback=on_progress,
                max_workers=8,
            )
            jobs[upload_key]["status"] = "completed"
            jobs[upload_key]["result"] = result
        except Exception as e:
            jobs[upload_key]["status"] = "error"
            jobs[upload_key]["error"] = str(e)

    asyncio.get_running_loop().run_in_executor(None, do_upload)
    return {"job_id": upload_key, "status": "uploading"}


@router.get("/r2/upload-status/{video_id}")
async def r2_upload_status(video_id: str):
    """Check R2 upload status"""
    key = f"r2_upload_{video_id}"
    if key not in jobs:
        raise HTTPException(404, "Upload not found")
    return jobs[key]


@router.get("/r2/videos")
async def r2_list_videos():
    """List videos in R2"""
    if not storage.configured:
        raise HTTPException(400, "R2 not configured")
    videos = storage.list_videos()
    return {"videos": videos, "count": len(videos)}


@router.delete("/r2/videos/{video_id}")
async def r2_delete_video(video_id: str):
    """Delete video from R2"""
    if not storage.configured:
        raise HTTPException(400, "R2 not configured")
    result = storage.delete_prefix(f"videos/{video_id}/")
    return result


# ═══════════════════════════════════════════════════════════
# PROCESS CANCELLATION
# ═══════════════════════════════════════════════════════════

@router.post("/cancel/{job_id}")
async def cancel_job(job_id: str):
    """Cancel a running processing job"""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    job = jobs[job_id]
    if job["status"] != "processing":
        return {"success": False, "error": f"Job is not processing (status: {job['status']})"}

    # Try to kill the FFmpeg process
    proc = _active_processes.get(job_id)
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        del _active_processes[job_id]

    job["status"] = "cancelled"
    job["error"] = "Cancelled by user"
    logger.info(f"Job {job_id} cancelled by user")
    return {"success": True, "job_id": job_id}


# ═══════════════════════════════════════════════════════════
# WEBSOCKET — Real-Time Progress
# ═══════════════════════════════════════════════════════════

# WebSocket connections for each job
_ws_connections: dict[str, list[WebSocket]] = {}


@router.websocket("/ws/status/{job_id}")
async def ws_status(websocket: WebSocket, job_id: str):
    """WebSocket endpoint for real-time processing progress"""
    await websocket.accept()

    # Register connection
    if job_id not in _ws_connections:
        _ws_connections[job_id] = []
    _ws_connections[job_id].append(websocket)

    try:
        while True:
            if job_id not in jobs:
                await websocket.send_json({"error": "Job not found"})
                break

            job = jobs[job_id]
            await websocket.send_json(job)

            if job["status"] in ("completed", "error", "cancelled"):
                break

            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"WebSocket error for job {job_id}: {e}")
    finally:
        if job_id in _ws_connections:
            try:
                _ws_connections[job_id].remove(websocket)
            except ValueError:
                pass
            if not _ws_connections[job_id]:
                del _ws_connections[job_id]


# ═══════════════════════════════════════════════════════════
# BATCH UPLOAD & AUTO-PROCESS
# ═══════════════════════════════════════════════════════════

# Batch storage
batches: dict = {}
_batch_lock = asyncio.Lock() if hasattr(asyncio, 'Lock') else None


class BatchSettings(BaseModel):
    """Default processing settings for batch"""
    qualities: list[str] = ["720p", "480p", "360p"]
    encoding_preset: str = "balanced"
    encoder: str = "libx264"
    segment_duration: int = 6
    threads: int = 0
    parallel: bool = False
    max_parallel: int = 2
    audio_bitrate: str = "128k"
    auto_upload_r2: bool = False

    @field_validator('encoding_preset')
    @classmethod
    def validate_encoding_preset(cls, v):
        if v not in ENCODING_PRESETS:
            raise ValueError(f'Unknown preset: {v}')
        return v

    @field_validator('qualities')
    @classmethod
    def validate_qualities(cls, v):
        valid = set(QualityProfile.ALL_PRESETS.keys())
        invalid = [q for q in v if q not in valid]
        if invalid:
            raise ValueError(f'Unknown qualities: {invalid}')
        return v


@router.post("/batch/upload")
async def batch_upload(
    files: list[UploadFile] = File(...),
    qualities: str = "720p,480p,360p",
    encoding_preset: str = "balanced",
    encoder: str = "libx264",
    segment_duration: int = 6,
    auto_upload_r2: bool = False,
):
    """
    Upload multiple videos and auto-process them sequentially.

    - Upload 1-20 video files at once
    - Each file is uploaded, probed, then queued for processing
    - Processing runs one-at-a-time to prevent CPU overload
    - Returns a batch_id to track overall progress
    """
    if len(files) > 20:
        raise HTTPException(400, "Maximum 20 files per batch")
    if len(files) < 1:
        raise HTTPException(400, "At least 1 file required")

    allowed = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".3gp"}
    quality_list = [q.strip() for q in qualities.split(",")]

    batch_id = str(uuid.uuid4())[:8]
    batch_items = []
    upload_errors = []

    for file in files:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in allowed:
            upload_errors.append({"file": file.filename, "error": f"Unsupported format: {ext}"})
            continue

        video_id = str(uuid.uuid4())[:8]
        upload_path = Path("./uploads") / f"{video_id}{ext}"

        try:
            total_size = 0
            with open(upload_path, "wb") as f:
                while True:
                    chunk = await file.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > MAX_UPLOAD_SIZE:
                        os.remove(upload_path)
                        upload_errors.append({"file": file.filename, "error": "File too large"})
                        break
                    f.write(chunk)
                else:
                    # Probe video
                    try:
                        info = processor.probe(str(upload_path))
                        batch_items.append({
                            "video_id": video_id,
                            "filename": file.filename,
                            "file_path": str(upload_path),
                            "info": info,
                            "status": "queued",
                            "job_id": None,
                            "error": None,
                        })
                    except ValueError as e:
                        os.remove(upload_path)
                        upload_errors.append({"file": file.filename, "error": str(e)})
        except Exception as e:
            if upload_path.exists():
                os.remove(upload_path)
            upload_errors.append({"file": file.filename, "error": str(e)})

    if not batch_items:
        raise HTTPException(400, f"No valid files uploaded. Errors: {upload_errors}")

    batches[batch_id] = {
        "batch_id": batch_id,
        "status": "processing",
        "total": len(batch_items),
        "completed": 0,
        "failed": 0,
        "current_index": 0,
        "items": batch_items,
        "upload_errors": upload_errors,
        "settings": {
            "qualities": quality_list,
            "encoding_preset": encoding_preset,
            "encoder": encoder,
            "segment_duration": segment_duration,
            "auto_upload_r2": auto_upload_r2,
        },
        "started_at": _time.time(),
        "finished_at": None,
    }

    logger.info(f"Batch {batch_id}: {len(batch_items)} videos queued for processing")

    # Start sequential processing in background
    asyncio.get_running_loop().run_in_executor(
        None, _process_batch, batch_id,
    )

    return {
        "batch_id": batch_id,
        "total_files": len(batch_items),
        "upload_errors": upload_errors,
        "status": "processing",
    }


def _process_batch(batch_id: str):
    """Process all videos in a batch sequentially"""
    batch = batches.get(batch_id)
    if not batch:
        return

    settings = batch["settings"]

    for idx, item in enumerate(batch["items"]):
        # Check if batch was cancelled
        if batch["status"] == "cancelled":
            item["status"] = "cancelled"
            continue

        batch["current_index"] = idx
        item["status"] = "processing"

        # Create a ProcessRequest for this item
        job_id = str(uuid.uuid4())[:8]
        item["job_id"] = job_id

        started_at = _time.time()

        jobs[job_id] = {
            "job_id": job_id,
            "video_id": item["video_id"],
            "status": "processing",
            "progress": {
                "step": 0,
                "total": len(settings["qualities"]) + 2,
                "status": f"Batch [{idx+1}/{len(batch['items'])}] Starting...",
                "percent": 0,
                "elapsed": 0,
                "eta": "—",
                "eta_seconds": 0,
            },
            "started_at": started_at,
            "result": None,
            "error": None,
            "batch_id": batch_id,
        }

        def update_progress(progress: dict, jid=job_id, i=idx, total=len(batch["items"])):
            if jid in jobs:
                progress["status"] = f"Batch [{i+1}/{total}] {progress.get('status', '')}"
                jobs[jid]["progress"] = progress

        try:
            result = processor.process(
                input_file=item["file_path"],
                video_id=item["video_id"],
                qualities=settings["qualities"],
                segment_duration=settings["segment_duration"],
                generate_thumbnail=True,
                progress_callback=update_progress,
                encoder=settings["encoder"],
                threads=0,
                parallel=False,
                max_parallel=2,
                encoding_preset=settings["encoding_preset"],
                audio_bitrate="128k",
            )

            jobs[job_id]["status"] = "completed"
            jobs[job_id]["result"] = result
            item["status"] = "completed"
            item["result"] = {
                "manifest_url": result.get("manifest_url") if isinstance(result, dict) else None,
                "stats": result.get("stats") if isinstance(result, dict) else None,
            }
            batch["completed"] += 1
            logger.info(f"Batch {batch_id}: [{idx+1}/{len(batch['items'])}] {item['filename']} completed")

        except Exception as e:
            logger.exception(f"Batch {batch_id}: [{idx+1}/{len(batch['items'])}] {item['filename']} failed: {e}")
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(e)
            item["status"] = "error"
            item["error"] = str(e)
            batch["failed"] += 1

    # Batch complete
    batch["status"] = "completed"
    batch["finished_at"] = _time.time()
    elapsed = batch["finished_at"] - batch["started_at"]
    logger.info(
        f"Batch {batch_id} finished: {batch['completed']}/{batch['total']} success, "
        f"{batch['failed']} failed, {elapsed:.1f}s total"
    )

    send_webhook("batch.completed", {
        "batch_id": batch_id,
        "total": batch["total"],
        "completed": batch["completed"],
        "failed": batch["failed"],
        "elapsed_seconds": elapsed,
    })


@router.get("/batch/status/{batch_id}")
async def batch_status(batch_id: str):
    """Get batch processing status"""
    if batch_id not in batches:
        raise HTTPException(404, "Batch not found")

    batch = batches[batch_id]

    # Calculate overall progress
    total_items = batch["total"]
    done = batch["completed"] + batch["failed"]
    percent = int((done / total_items) * 100) if total_items > 0 else 0

    # Get current item's progress
    current_progress = None
    if batch["status"] == "processing" and batch["current_index"] < len(batch["items"]):
        current_item = batch["items"][batch["current_index"]]
        if current_item.get("job_id") and current_item["job_id"] in jobs:
            current_progress = jobs[current_item["job_id"]].get("progress")

    return {
        "batch_id": batch_id,
        "status": batch["status"],
        "total": total_items,
        "completed": batch["completed"],
        "failed": batch["failed"],
        "percent": percent,
        "current_index": batch["current_index"],
        "current_progress": current_progress,
        "items": [
            {
                "filename": item["filename"],
                "video_id": item["video_id"],
                "status": item["status"],
                "job_id": item.get("job_id"),
                "error": item.get("error"),
                "result": item.get("result"),
            }
            for item in batch["items"]
        ],
        "upload_errors": batch["upload_errors"],
        "elapsed": _time.time() - batch["started_at"],
    }


@router.get("/batch/list")
async def list_batches():
    """List all batches"""
    return {
        "batches": [
            {
                "batch_id": b["batch_id"],
                "status": b["status"],
                "total": b["total"],
                "completed": b["completed"],
                "failed": b["failed"],
                "started_at": b["started_at"],
            }
            for b in batches.values()
        ]
    }


@router.post("/batch/cancel/{batch_id}")
async def cancel_batch(batch_id: str):
    """Cancel a running batch"""
    if batch_id not in batches:
        raise HTTPException(404, "Batch not found")

    batch = batches[batch_id]
    if batch["status"] != "processing":
        return {"success": False, "error": f"Batch is {batch['status']}"}

    batch["status"] = "cancelled"

    # Cancel current processing job
    current_item = batch["items"][batch["current_index"]]
    if current_item.get("job_id"):
        proc = _active_processes.get(current_item["job_id"])
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass

    logger.info(f"Batch {batch_id} cancelled")
    return {"success": True, "batch_id": batch_id}
