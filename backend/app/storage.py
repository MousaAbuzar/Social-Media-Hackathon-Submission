"""Object storage for run artifacts.

Audio does not belong in Postgres. Files land in S3-compatible storage and the
API hands out presigned URLs so downloads never proxy through the app server.
"""

from functools import lru_cache

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import get_settings


def _build_client(endpoint_url: str):
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or None,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )


@lru_cache
def _client():
    """Client for server-side calls (put/head), using the internal endpoint."""
    return _build_client(get_settings().s3_endpoint_url)


@lru_cache
def _public_client():
    """Client used only to sign download URLs.

    A presigned URL's signature covers the host, so it must be generated
    against the endpoint the *browser* will use. In Compose the API reaches
    MinIO at http://minio:9000, which no browser can resolve.
    """
    settings = get_settings()
    return _build_client(settings.s3_public_endpoint_url or settings.s3_endpoint_url)


def ensure_bucket() -> None:
    settings = get_settings()
    client = _client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)


def put_object(key: str, data: bytes, content_type: str) -> int:
    settings = get_settings()
    ensure_bucket()
    _client().put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type)
    return len(data)


CHUNK_BYTES = 256 * 1024


def iter_object(key: str):
    """Stream an object back through the internal endpoint, in chunks.

    Used to serve downloads from the API instead of pointing the browser at
    storage directly. Presigned URLs are the cheaper path in principle, but
    they put the browser on a second origin with its own CORS rules, its own
    expiry, and its own failure modes.

    Chunked rather than read whole: narration WAVs run to tens of megabytes,
    and buffering one entirely per request costs that much API memory for as
    long as the transfer takes.
    """
    settings = get_settings()
    body = _client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"]
    try:
        while chunk := body.read(CHUNK_BYTES):
            yield chunk
    finally:
        body.close()


def object_size(key: str) -> int:
    settings = get_settings()
    return _client().head_object(Bucket=settings.s3_bucket, Key=key)["ContentLength"]


def presigned_url(key: str, expires_seconds: int = 3600) -> str:
    settings = get_settings()
    return _public_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=expires_seconds,
    )
