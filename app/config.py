# ═══════════════════════════════════════════════════════════
# StreamForge — Configuration Manager
# ═══════════════════════════════════════════════════════════

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()


class Config:
    """Centralized configuration from environment variables"""

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    WORKERS: int = int(os.getenv("WORKERS", "1"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info")

    # Security
    API_KEY: str = os.getenv("API_KEY", "")
    RATE_LIMIT: str = os.getenv("RATE_LIMIT", "30/minute")

    # Storage
    UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR", "./uploads"))
    OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", "./output"))

    # Cloudflare R2
    R2_ACCOUNT_ID: str = os.getenv("R2_ACCOUNT_ID", "")
    R2_ACCESS_KEY: str = os.getenv("R2_ACCESS_KEY", "")
    R2_SECRET_KEY: str = os.getenv("R2_SECRET_KEY", "")
    R2_BUCKET: str = os.getenv("R2_BUCKET", "")
    R2_PUBLIC_URL: str = os.getenv("R2_PUBLIC_URL", "")

    # Webhook
    WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
    WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")

    # Limits
    MAX_UPLOAD_SIZE: int = int(float(os.getenv("MAX_UPLOAD_SIZE_GB", "2")) * 1024 * 1024 * 1024)
    MAX_JOBS: int = int(os.getenv("MAX_JOBS", "100"))
    JOB_TTL_SECONDS: int = int(float(os.getenv("JOB_TTL_HOURS", "1")) * 3600)

    @classmethod
    def has_api_key(cls) -> bool:
        return bool(cls.API_KEY)

    @classmethod
    def has_r2(cls) -> bool:
        return bool(cls.R2_ACCOUNT_ID and cls.R2_ACCESS_KEY and cls.R2_SECRET_KEY and cls.R2_BUCKET)

    @classmethod
    def has_webhook(cls) -> bool:
        return bool(cls.WEBHOOK_URL)


config = Config()
