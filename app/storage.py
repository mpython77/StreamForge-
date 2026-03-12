"""
StreamForge Cloud Storage — Cloudflare R2 Integration

R2 = S3-compatible object storage by Cloudflare
- $0.015/GB/month (3x cheaper than S3)
- Egress FREE (via CDN)
- S3 API compatible (works with boto3)

Setup:
1. Cloudflare Dashboard → R2 → Create Bucket
2. R2 → Manage R2 API Tokens → Create Token
3. From Token: Account ID, Access Key ID, Secret Access Key
"""

import boto3
from botocore.config import Config as BotoConfig
import os
import mimetypes
import logging
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("streamforge.storage")


class CloudStorage:
    """Cloudflare R2 / S3-compatible storage"""

    def __init__(self):
        self.client = None
        self.bucket = None
        self.public_url = None
        self.configured = False

    def configure(self, account_id: str, access_key: str, secret_key: str,
                  bucket: str, public_url: str = ""):
        """
        Configure R2 storage.

        Args:
            account_id: Cloudflare Account ID
            access_key: R2 API Access Key ID
            secret_key: R2 API Secret Access Key
            bucket: R2 bucket name
            public_url: Public domain (e.g. https://cdn.example.com)
        """
        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
        self.bucket = bucket
        self.public_url = public_url.rstrip("/") if public_url else endpoint
        self.configured = True
        logger.info(f"R2 configured: bucket={bucket}, endpoint={endpoint}")
        return True

    def test_connection(self) -> dict:
        """Test R2 connection"""
        if not self.configured:
            return {"ok": False, "error": "R2 not configured"}
        try:
            response = self.client.head_bucket(Bucket=self.bucket)
            return {"ok": True, "bucket": self.bucket}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def upload_file(self, local_path: str, remote_key: str,
                    content_type: str = None) -> dict:
        """Upload a single file"""
        if not self.configured:
            return {"ok": False, "error": "R2 not configured"}

        if not content_type:
            content_type = self._guess_content_type(local_path)

        try:
            extra_args = {"ContentType": content_type}

            # CORS and cache headers for HLS files
            if local_path.endswith(".m3u8"):
                extra_args["ContentType"] = "application/vnd.apple.mpegurl"
                extra_args["CacheControl"] = "no-cache, no-store"
            elif local_path.endswith(".ts"):
                extra_args["ContentType"] = "video/mp2t"
                extra_args["CacheControl"] = "public, max-age=31536000"
            elif local_path.endswith(".vtt"):
                extra_args["ContentType"] = "text/vtt"
            elif local_path.endswith(".jpg") or local_path.endswith(".jpeg"):
                extra_args["CacheControl"] = "public, max-age=86400"

            self.client.upload_file(
                local_path, self.bucket, remote_key,
                ExtraArgs=extra_args,
            )

            url = f"{self.public_url}/{remote_key}"
            return {"ok": True, "key": remote_key, "url": url}
        except Exception as e:
            logger.error(f"Upload failed: {local_path} -> {e}")
            return {"ok": False, "error": str(e)}

    def upload_directory(self, local_dir: str, remote_prefix: str,
                         progress_callback=None, max_workers: int = 8) -> dict:
        """
        Upload entire directory to R2 (parallel).

        Args:
            local_dir: Local directory path (e.g. ./output/abc123)
            remote_prefix: R2 directory prefix (e.g. videos/abc123)
            progress_callback: Progress callback (uploaded, total, filename)
            max_workers: Number of parallel upload workers
        """
        if not self.configured:
            return {"ok": False, "error": "R2 not configured"}

        local_path = Path(local_dir)
        if not local_path.exists():
            return {"ok": False, "error": f"Directory not found: {local_dir}"}

        # Collect all files
        files = [f for f in local_path.rglob("*") if f.is_file()]
        total = len(files)
        if total == 0:
            return {"ok": False, "error": "Empty directory"}

        uploaded = 0
        failed = 0
        total_bytes = 0
        results = []

        def upload_one(file_path):
            rel = file_path.relative_to(local_path)
            key = f"{remote_prefix}/{rel.as_posix()}"
            return self.upload_file(str(file_path), key)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(upload_one, f): f for f in files}
            for future in as_completed(futures):
                file_path = futures[future]
                try:
                    result = future.result()
                    if result["ok"]:
                        uploaded += 1
                        total_bytes += file_path.stat().st_size
                    else:
                        failed += 1
                    results.append(result)
                except Exception as e:
                    failed += 1
                    results.append({"ok": False, "error": str(e)})

                if progress_callback:
                    progress_callback(uploaded + failed, total, file_path.name)

        master_url = f"{self.public_url}/{remote_prefix}/master.m3u8"

        return {
            "ok": failed == 0,
            "uploaded": uploaded,
            "failed": failed,
            "total": total,
            "total_mb": round(total_bytes / 1048576, 2),
            "master_url": master_url,
            "prefix": remote_prefix,
        }

    def delete_prefix(self, prefix: str) -> dict:
        """Delete all objects under a prefix"""
        if not self.configured:
            return {"ok": False, "error": "R2 not configured"}
        try:
            response = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
            objects = response.get("Contents", [])
            if not objects:
                return {"ok": True, "deleted": 0}

            delete_keys = [{"Key": obj["Key"]} for obj in objects]
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": delete_keys},
            )
            return {"ok": True, "deleted": len(delete_keys)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_videos(self, prefix: str = "videos/") -> list:
        """List videos stored in R2"""
        if not self.configured:
            return []
        try:
            response = self.client.list_objects_v2(
                Bucket=self.bucket, Prefix=prefix, Delimiter="/",
            )
            folders = []
            for cp in response.get("CommonPrefixes", []):
                folder = cp["Prefix"].rstrip("/").split("/")[-1]
                folders.append({
                    "video_id": folder,
                    "master_url": f"{self.public_url}/{cp['Prefix']}master.m3u8",
                    "prefix": cp["Prefix"],
                })
            return folders
        except Exception as e:
            logger.error(f"List failed: {e}")
            return []

    @staticmethod
    def _guess_content_type(path: str) -> str:
        ct, _ = mimetypes.guess_type(path)
        return ct or "application/octet-stream"


# Singleton
storage = CloudStorage()
