# ═══════════════════════════════════════════════════════════
# StreamForge v3.0 — Multi-Stage Docker Build
# ═══════════════════════════════════════════════════════════
# Usage:
#   docker build -t streamforge .
#   docker run -p 8000:8000 streamforge
# ═══════════════════════════════════════════════════════════

FROM python:3.12-slim AS base

# Install FFmpeg and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY static/ ./static/

# Create required directories
RUN mkdir -p uploads output logs

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run with production settings
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
