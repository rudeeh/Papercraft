"""
Object storage for canonical PDFs (MinIO, or any S3-compatible endpoint).

Follows the same optional-datastore contract as Neo4j and Weaviate: if the
endpoint is unreachable or boto3 isn't installed, ``ObjectStore.connect()``
raises and the caller falls back to the local ``UPLOAD_DIR``. Nothing in
the ingestion path *requires* object storage -- it makes uploads durable
across container rebuilds and shareable between the api and worker
replicas, which bind-mounting a volume only fakes.

boto3 is imported inside ``connect()`` rather than at module scope so that
``OBJECT_STORE_ENABLED=false`` deployments (the default) need not install
it at all.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()


class ObjectStoreError(RuntimeError):
    """Raised when the object store is unreachable or rejects an operation."""


class ObjectStore:
    """Minimal S3 wrapper: ensure bucket, put, get, exists, presign, delete."""

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
    ) -> None:
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.region = region
        self._client = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise ObjectStoreError(
                "boto3 is not installed; set OBJECT_STORE_ENABLED=false or "
                "install the object-store extra"
            ) from exc

        try:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
                # MinIO speaks SigV4 and path-style addressing; virtual-host
                # style would resolve "<bucket>.minio" and fail inside compose.
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
        except Exception as exc:
            raise ObjectStoreError(f"Could not create S3 client: {exc}") from exc

        self.ensure_bucket()
        logger.info("object_store_connected", endpoint=self.endpoint_url, bucket=self.bucket)

    @property
    def client(self):
        if self._client is None:
            raise ObjectStoreError("ObjectStore.connect() has not been called")
        return self._client

    def close(self) -> None:
        self._client = None

    # ------------------------------------------------------------------
    # Bucket / object operations
    # ------------------------------------------------------------------

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not already exist (idempotent)."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return
        except Exception:
            pass  # Missing or inaccessible -- try to create, and report *that* error.

        try:
            # us-east-1 is the one region where passing a LocationConstraint
            # is an error rather than a requirement; MinIO mimics that.
            if self.region and self.region != "us-east-1":
                self.client.create_bucket(
                    Bucket=self.bucket,
                    CreateBucketConfiguration={"LocationConstraint": self.region},
                )
            else:
                self.client.create_bucket(Bucket=self.bucket)
            logger.info("object_store_bucket_created", bucket=self.bucket)
        except Exception as exc:
            if "BucketAlreadyOwnedByYou" in str(exc) or "BucketAlreadyExists" in str(exc):
                return
            raise ObjectStoreError(f"Could not ensure bucket '{self.bucket}': {exc}") from exc

    def put_file(
        self, key: str, file_path: str, content_type: str = "application/pdf"
    ) -> str:
        try:
            self.client.upload_file(
                Filename=file_path,
                Bucket=self.bucket,
                Key=key,
                ExtraArgs={"ContentType": content_type},
            )
        except Exception as exc:
            raise ObjectStoreError(f"Upload of '{key}' failed: {exc}") from exc
        return key

    def get_to_file(self, key: str, file_path: str) -> str:
        try:
            self.client.download_file(Bucket=self.bucket, Key=key, Filename=file_path)
        except Exception as exc:
            raise ObjectStoreError(f"Download of '{key}' failed: {exc}") from exc
        return file_path

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise ObjectStoreError(f"Delete of '{key}' failed: {exc}") from exc

    def presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_seconds,
            )
        except Exception as exc:
            raise ObjectStoreError(f"Could not presign '{key}': {exc}") from exc

    def list_keys(self, prefix: str = "") -> List[str]:
        keys: List[str] = []
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys.extend(obj["Key"] for obj in page.get("Contents", []))
        except Exception as exc:
            raise ObjectStoreError(f"Listing '{prefix}' failed: {exc}") from exc
        return keys


def create_object_store(settings_obj: Optional[Any] = None) -> Optional[ObjectStore]:
    """
    Build and connect an ObjectStore from settings, or return None if
    object storage is disabled or unreachable. Mirrors
    ``_create_neo4j_client`` in app/worker/tasks.py: never raises, so a
    missing MinIO degrades to local-disk storage instead of failing uploads.
    """
    from app.core.config import settings as default_settings

    cfg = settings_obj or default_settings
    if not cfg.OBJECT_STORE_ENABLED:
        return None

    store = ObjectStore(
        endpoint_url=cfg.MINIO_ENDPOINT,
        access_key=cfg.MINIO_ACCESS_KEY,
        secret_key=cfg.MINIO_SECRET_KEY,
        bucket=cfg.MINIO_BUCKET_PDFS,
        region=cfg.MINIO_REGION,
    )
    try:
        store.connect()
        return store
    except ObjectStoreError as exc:
        logger.warning("object_store_unavailable", error=str(exc))
        return None


def pdf_key(doc_id: str) -> str:
    """Canonical object key for an ingested PDF."""
    return f"pdfs/{doc_id}.pdf"
