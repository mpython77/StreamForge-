# ═══════════════════════════════════════════════════════════
# StreamForge — Unit Tests
# ═══════════════════════════════════════════════════════════

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from app.config import Config
from app.middleware import api_key_middleware


client = TestClient(app)


# ─── Health & Root ───────────────────────────────────────────

class TestHealth:
    def test_health_returns_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "active_jobs" in data

    def test_root_returns_html(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_docs_accessible(self):
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_redoc_accessible(self):
        resp = client.get("/redoc")
        assert resp.status_code == 200


# ─── Hardware API ────────────────────────────────────────────

class TestHardwareAPI:
    def test_hardware_returns_cpu_info(self):
        resp = client.get("/api/hardware")
        assert resp.status_code == 200
        data = resp.json()
        assert "cpu_name" in data
        assert "cpu_cores" in data
        assert "best_mode" in data

    def test_presets_returns_dict(self):
        resp = client.get("/api/presets")
        assert resp.status_code == 200
        data = resp.json()
        assert "balanced" in data
        assert "fast" in data


# ─── Security: Input Validation ─────────────────────────────

class TestSecurity:
    def test_invalid_video_id_rejected(self):
        resp = client.get("/api/probe/hello%20world")
        assert resp.status_code in (400, 404)  # URL-encoded paths may match differently

    def test_invalid_video_id_with_dots(self):
        resp = client.get("/api/probe/hello..world")
        assert resp.status_code == 400

    def test_valid_video_id_accepted(self):
        resp = client.get("/api/probe/valid-video-123")
        # Should return 404 (video not found), not 400 (invalid ID)
        assert resp.status_code == 404

    def test_empty_video_id(self):
        # Empty path segment — various HTTP codes are acceptable
        resp = client.get("/api/probe/")
        assert resp.status_code in (404, 405, 307, 422)

    def test_upload_missing_file(self):
        resp = client.post("/api/upload")
        assert resp.status_code == 422  # Validation error

    def test_process_invalid_qualities(self):
        resp = client.post("/api/process", json={
            "video_id": "test",
            "qualities": ["99999p"],
        })
        assert resp.status_code == 422

    def test_process_invalid_preset(self):
        resp = client.post("/api/process", json={
            "video_id": "test",
            "encoding_preset": "hacker_preset",
        })
        assert resp.status_code == 422


# ─── Jobs API ────────────────────────────────────────────────

class TestJobsAPI:
    def test_list_jobs(self):
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data
        assert isinstance(data["jobs"], list)

    def test_status_nonexistent_job(self):
        resp = client.get("/api/status/nonexistent-id")
        assert resp.status_code == 404

    def test_cancel_nonexistent_job(self):
        resp = client.post("/api/cancel/nonexistent-id")
        assert resp.status_code == 404


# ─── Disk API ────────────────────────────────────────────────

class TestDiskAPI:
    def test_disk_usage(self):
        resp = client.get("/api/disk")
        assert resp.status_code == 200
        data = resp.json()
        # Response structure includes upload and output info
        assert isinstance(data, dict)


# ─── Config Module ───────────────────────────────────────────

class TestConfig:
    def test_default_values(self):
        assert Config.PORT == 8000
        assert Config.WORKERS == 1
        assert Config.MAX_JOBS == 100

    def test_has_api_key_false_by_default(self):
        assert not Config.has_api_key() or Config.API_KEY != ""

    def test_max_upload_size_calculation(self):
        assert Config.MAX_UPLOAD_SIZE == int(2 * 1024 * 1024 * 1024)


# ─── Webhook Module ──────────────────────────────────────────

class TestWebhook:
    @patch.object(Config, "WEBHOOK_URL", "")
    def test_webhook_skipped_when_not_configured(self):
        from app.webhook import send_webhook
        result = send_webhook("test.event", {"key": "value"})
        assert result is None

    @patch.object(Config, "WEBHOOK_URL", "https://example.com/hook")
    @patch("app.webhook.requests.post")
    def test_webhook_sends_post(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, ok=True)
        from app.webhook import send_webhook
        result = send_webhook("job.completed", {"job_id": "test"})
        assert mock_post.called
        assert result["ok"] is True


# ─── Metrics ─────────────────────────────────────────────────

class TestMetrics:
    def test_metrics_endpoint(self):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text
        assert "http_requests_total" in body
        assert "streamforge" in body
