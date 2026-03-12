<div align="center">

# ⚡ StreamForge

**Transform any video into HLS adaptive bitrate streams**

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-5.0+-007808?style=flat-square&logo=ffmpeg&logoColor=white)](https://ffmpeg.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=flat-square&logo=docker&logoColor=white)](Dockerfile)

Multi-quality HLS encoding • Hardware acceleration • Batch processing • R2 cloud upload • Real-time WebSocket progress

---

</div>

## ✨ Features

| Feature | Description |
| --- | --- |
| **Multi-Quality HLS** | Single-decode multi-encode pipeline (4K → 144p) |
| **Hardware Acceleration** | Auto-detect NVIDIA NVENC, AMD AMF, Intel QSV |
| **Batch Processing** | Upload 1-20 videos, auto-process sequentially |
| **WebSocket Progress** | Real-time updates via WebSocket (polling fallback) |
| **Cloudflare R2** | One-click upload to R2 with public CDN URLs |
| **Process Cancellation** | Cancel running FFmpeg jobs mid-process |
| **API Key Auth** | Optional token-based API authentication |
| **Rate Limiting** | Configurable per-IP rate limits |
| **Prometheus Metrics** | `/metrics` endpoint for monitoring |
| **Webhook Notifications** | HMAC-SHA256 signed callbacks on job completion |
| **Docker Support** | Production-ready Dockerfile + docker-compose |

## 🚀 Quick Start

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/streamforge.git
cd streamforge

# Install
pip install -r requirements.txt

# Run
uvicorn app.main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000)

### Docker

```bash
docker compose up -d
```

## 🏗️ Architecture

```
streamforge/
├── app/
│   ├── main.py          # FastAPI app + middleware stack
│   ├── routes.py         # API endpoints + WebSocket + Batch
│   ├── processor.py      # FFmpeg processing engine
│   ├── hardware.py       # Cross-platform hardware detection
│   ├── storage.py        # Cloudflare R2 integration
│   ├── config.py         # .env configuration manager
│   ├── middleware.py      # Rate limiting + API key auth
│   ├── metrics.py        # Prometheus metrics
│   └── webhook.py        # Webhook notifications
├── static/               # Web UI (HTML + JS + CSS)
├── tests/                # pytest test suite
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## 📡 API Reference

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/health` | Health check + version |
| `GET` | `/docs` | Swagger UI documentation |
| `GET` | `/metrics` | Prometheus metrics |
| `POST` | `/api/upload` | Upload video file |
| `POST` | `/api/process` | Start processing |
| `POST` | `/api/batch/upload` | Batch upload + auto-process |
| `GET` | `/api/batch/status/{id}` | Batch progress |
| `GET` | `/api/status/{id}` | Job status |
| `POST` | `/api/cancel/{id}` | Cancel job |
| `WS` | `/ws/status/{id}` | WebSocket progress |
| `POST` | `/api/r2/upload/{id}` | Upload to R2 CDN |

> Full API docs available at `/docs` when the server is running.

## ⚙️ Configuration

Copy `.env.example` to `.env` and customize:

```env
# Security (optional)
API_KEY=your-secret-key
RATE_LIMIT=30/minute

# Cloudflare R2 (optional)
R2_ACCOUNT_ID=...
R2_ACCESS_KEY=...
R2_SECRET_KEY=...
R2_BUCKET=my-bucket

# Webhook (optional)
WEBHOOK_URL=https://your-api.com/hook
WEBHOOK_SECRET=signing-secret
```

All settings have sensible defaults — zero config needed to start.

## 🧪 Testing

```bash
pytest tests/ -v
```

## 🔒 Security

- Optional API key authentication (`X-API-Key` header)
- Configurable rate limiting per IP
- Path traversal protection with regex validation
- Streaming upload (1MB chunks, no full file in RAM)
- HMAC-SHA256 signed webhook payloads

## 📋 Requirements

- Python 3.10+
- FFmpeg 5.0+ with ffprobe
- 4GB+ RAM recommended for 4K processing

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📄 License

[MIT](LICENSE) — Use it freely in your projects.
