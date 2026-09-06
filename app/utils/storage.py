"""
File storage abstraction.

StorageBackend protocol defines the interface.
LocalStorage and S3Storage are concrete implementations.
The active backend is selected at startup via STORAGE_BACKEND env var.

Usage:
    from app.utils.storage import get_storage
    storage = get_storage()
    url = await storage.upload(file, "reports/2024/report.pdf")
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol, runtime_checkable

import boto3
from botocore.config import Config
from fastapi import UploadFile

from app.config import settings


@runtime_checkable
class StorageBackend(Protocol):
    async def upload(self, file: UploadFile, path: str) -> str:
        """Upload a file and return its storage path or URL."""
        ...

    def get_url(self, path: str) -> str:
        """Get the public URL for a stored file path."""
        ...

    def generate_presigned_url(self, path: str, expires_in: int = 600) -> str:
        """Generate a short-lived presigned URL for secure download (default 10 min)."""
        ...

    async def delete(self, path: str) -> None:
        """Delete a stored file."""
        ...


class LocalStorage:
    """
    Stores files on the local filesystem under LOCAL_UPLOAD_DIR.
    Suitable for development; returns a relative URL path.
    """

    def __init__(self) -> None:
        self.base_dir = Path(settings.LOCAL_UPLOAD_DIR)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def upload(self, file: UploadFile, path: str) -> str:
        dest = self.base_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        dest.write_bytes(content)
        return path

    def get_url(self, path: str) -> str:
        return f"/static/{path}"

    def generate_presigned_url(self, path: str, expires_in: int = 600) -> str:
        # In local development, return local route with expiration parameter
        return f"/static/{path}?expires_in={expires_in}"

    async def delete(self, path: str) -> None:
        dest = self.base_dir / path
        if dest.exists():
            dest.unlink()


class S3Storage:
    """
    Stores files in an S3-compatible bucket (Cloudflare R2 by default).
    Returns internal path and short-lived presigned download URLs.
    """

    def __init__(self) -> None:
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4"),
        )
        self.bucket = settings.S3_BUCKET_NAME
        self.public_url = settings.S3_PUBLIC_URL.rstrip("/")

    async def upload(self, file: UploadFile, path: str) -> str:
        content = await file.read()
        self.client.put_object(
            Bucket=self.bucket,
            Key=path,
            Body=content,
            ContentType=file.content_type or "application/octet-stream",
        )
        return path

    def get_url(self, path: str) -> str:
        return f"{self.public_url}/{path}"

    def generate_presigned_url(self, path: str, expires_in: int = 600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": path},
            ExpiresIn=expires_in,
        )

    async def delete(self, path: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=path)


# ── Singleton factory ──────────────────────────────────────────────────────────
_storage_instance: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """Return the configured storage backend (singleton)."""
    global _storage_instance
    if _storage_instance is None:
        if settings.STORAGE_BACKEND == "s3":
            _storage_instance = S3Storage()
        else:
            _storage_instance = LocalStorage()
    return _storage_instance


def generate_upload_path(folder: str, filename: str) -> str:
    """Generate a unique, collision-safe storage path."""
    ext = Path(filename).suffix.lower()
    unique_name = f"{uuid.uuid4().hex}{ext}"
    return f"{folder}/{unique_name}"
