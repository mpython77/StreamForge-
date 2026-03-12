# ═══════════════════════════════════════════════════════════
# StreamForge — Webhook Notifications
# ═══════════════════════════════════════════════════════════

import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import requests

from .config import config

logger = logging.getLogger("streamforge")


def send_webhook(event: str, payload: dict) -> Optional[dict]:
    """
    Send webhook notification for job events.

    Events: job.completed, job.error, job.cancelled, upload.completed
    """
    if not config.has_webhook():
        return None

    body = {
        "event": event,
        "timestamp": int(time.time()),
        "data": payload,
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "StreamForge/4.0",
        "X-StreamForge-Event": event,
    }

    # Sign payload if secret is configured
    if config.WEBHOOK_SECRET:
        raw = json.dumps(body, separators=(",", ":"), sort_keys=True)
        signature = hmac.new(
            config.WEBHOOK_SECRET.encode(),
            raw.encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["X-StreamForge-Signature"] = f"sha256={signature}"

    try:
        resp = requests.post(
            config.WEBHOOK_URL,
            json=body,
            headers=headers,
            timeout=10,
        )
        logger.info(f"Webhook sent: {event} → {resp.status_code}")
        return {"status": resp.status_code, "ok": resp.ok}
    except requests.RequestException as e:
        logger.warning(f"Webhook failed: {event} → {e}")
        return {"status": 0, "ok": False, "error": str(e)}
