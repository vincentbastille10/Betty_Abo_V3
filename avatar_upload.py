# avatar_upload.py
from __future__ import annotations

import os
import uuid
import mimetypes
import logging
from typing import Optional, BinaryIO

logger = logging.getLogger(__name__)

# Recommandé (Cloudflare R2 S3-compatible)
# Env vars attendues:
# - R2_ENDPOINT_URL (ex: https://<accountid>.r2.cloudflarestorage.com)
# - R2_ACCESS_KEY_ID
# - R2_SECRET_ACCESS_KEY
# - R2_BUCKET
# - R2_PUBLIC_BASE_URL (ex: https://pub-xxxxx.r2.dev ou ton domaine CDN)
#
# Option: AVATAR_PREFIX (default: "avatars")


def _require_boto3():
    try:
        import boto3  # noqa
        return True
    except Exception:
        return False


def upload_avatar_to_r2(fileobj: BinaryIO, filename: str, *, content_type: Optional[str] = None) -> str:
    """
    Upload un avatar vers Cloudflare R2 (S3 compatible).
    Retourne l'URL publique (R2_PUBLIC_BASE_URL + key)

    NOTE: Sur Vercel, on évite le stockage local => R2/S3 est idéal.
    """
    if not _require_boto3():
        raise RuntimeError("boto3 manquant. Installe boto3 pour activer l'upload R2.")

    import boto3  # type: ignore

    endpoint = os.getenv("R2_ENDPOINT_URL")
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    bucket = os.getenv("R2_BUCKET")
    public_base = os.getenv("R2_PUBLIC_BASE_URL")
    prefix = os.getenv("AVATAR_PREFIX", "avatars")

    missing = [k for k, v in {
        "R2_ENDPOINT_URL": endpoint,
        "R2_ACCESS_KEY_ID": access_key,
        "R2_SECRET_ACCESS_KEY": secret_key,
        "R2_BUCKET": bucket,
        "R2_PUBLIC_BASE_URL": public_base
    }.items() if not v]
    if missing:
        raise RuntimeError(f"Env vars manquantes pour R2: {', '.join(missing)}")

    ext = (os.path.splitext(filename)[1] or "").lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        # on tolère quand même, mais mieux de limiter côté front
        logger.warning("Extension avatar non standard: %s", ext)

    if not content_type:
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    key = f"{prefix}/{uuid.uuid4().hex}{ext or ''}"

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )

    s3.upload_fileobj(
        Fileobj=fileobj,
        Bucket=bucket,
        Key=key,
        ExtraArgs={"ContentType": content_type},
    )

    # URL publique
    public_base = public_base.rstrip("/")
    return f"{public_base}/{key}"
