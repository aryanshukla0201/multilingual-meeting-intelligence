from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import MediaAsset, Meeting
from app.services.media_inspection import (
    MediaValidationError,
    inspect_media,
    safe_original_filename,
    validate_extension_and_mime,
)
from app.services.storage import StorageProvider
from app.settings import settings


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def secure_storage_filename(filename: str) -> str:
    basename = safe_original_filename(filename)
    sanitized = _SAFE_NAME.sub("_", basename).strip("._")
    if not sanitized:
        raise MediaValidationError("INVALID_FILENAME", "The filename is invalid")
    return sanitized


async def _stage_upload(upload: UploadFile) -> tuple[Path, int, str]:
    temporary = tempfile.NamedTemporaryFile(prefix="meeting-media-", suffix=".upload", delete=False)
    temporary_path = Path(temporary.name)
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary:
            while chunk := await upload.read(settings.upload_chunk_size):
                size += len(chunk)
                if size > settings.max_media_size_bytes:
                    raise MediaValidationError("MEDIA_TOO_LARGE", "Media exceeds the configured size limit", 413)
                digest.update(chunk)
                temporary.write(chunk)
        if size == 0:
            raise MediaValidationError("EMPTY_MEDIA", "The uploaded media is empty")
        return temporary_path, size, digest.hexdigest()
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


async def ingest_media(
    db: Session,
    meeting_id: str,
    upload: UploadFile,
    storage: StorageProvider,
) -> MediaAsset:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise MediaValidationError("MEETING_NOT_FOUND", "Meeting not found", 404)

    original_filename = safe_original_filename(upload.filename)
    extension = validate_extension_and_mime(original_filename, upload.content_type)
    temporary_path, size, checksum = await _stage_upload(upload)
    asset_id = str(uuid4())
    safe_filename = secure_storage_filename(original_filename)
    storage_key = f"meetings/{meeting_id}/media/{asset_id}/{safe_filename}"
    try:
        metadata = inspect_media(temporary_path, extension, settings.ffprobe_path)
        storage.put_file(temporary_path, storage_key)
        asset = MediaAsset(
            id=asset_id,
            meeting_id=meeting_id,
            filename=safe_filename,
            original_filename=original_filename,
            mime_type=upload.content_type or "application/octet-stream",
            size_bytes=size,
            duration_seconds=metadata.duration_seconds,
            codec=metadata.codec,
            sample_rate=metadata.sample_rate,
            channels=metadata.channels,
            resolution=(f"{metadata.width}x{metadata.height}" if metadata.width and metadata.height else None),
            width=metadata.width,
            height=metadata.height,
            fps=metadata.fps,
            checksum=checksum,
            storage_key=storage_key,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        return asset
    except MediaValidationError:
        raise
    except Exception as exc:
        db.rollback()
        try:
            storage.delete(storage_key)
        except Exception:
            pass
        raise MediaValidationError("MEDIA_INGESTION_FAILED", "Media could not be stored", 500) from exc
    finally:
        temporary_path.unlink(missing_ok=True)


def list_media(db: Session, meeting_id: str) -> list[MediaAsset]:
    return list(
        db.scalars(
            select(MediaAsset)
            .where(MediaAsset.meeting_id == meeting_id)
            .order_by(MediaAsset.created_at, MediaAsset.id)
        ).all()
    )


def get_media(db: Session, media_id: str) -> MediaAsset | None:
    return db.get(MediaAsset, media_id)


def delete_media(db: Session, media_id: str, storage: StorageProvider) -> bool:
    asset = db.get(MediaAsset, media_id)
    if asset is None:
        return False
    try:
        storage.delete(asset.storage_key)
        db.delete(asset)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise MediaValidationError("MEDIA_DELETE_FAILED", "Media could not be deleted", 500) from exc
    return True
