from __future__ import annotations

import tempfile
from pathlib import Path
from collections.abc import Iterator

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Meeting, MeetingStatus, SourceType
from app.schemas.contracts import MediaAssetRead, MeetingRead
from app.services.media_inspection import MediaValidationError
from app.services.media_service import delete_media, get_media, ingest_media, list_media
from app.services.storage import StorageError, StorageProvider, create_storage_provider

router = APIRouter(prefix="/api", tags=["media"])


def _success(data: object, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": True, "data": data, "error": None, "meta": {}},
    )


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "data": None, "error": {"code": code, "message": message}, "meta": {}},
    )


def _media_data(asset) -> dict:
    return MediaAssetRead.model_validate(asset).model_dump(mode="json")


def _meeting_data(meeting) -> dict:
    return MeetingRead.model_validate(meeting).model_dump(mode="json")


def _source_type(filename: str | None) -> SourceType:
    return SourceType.VIDEO if Path(filename or "").suffix.lower() in {".mp4", ".mov", ".webm"} else SourceType.AUDIO


def _storage() -> StorageProvider:
    return create_storage_provider()


@router.post("/meetings", status_code=201, summary="Create a meeting and upload its media")
async def create_meeting(
    title: str = Form(..., min_length=1, max_length=300),
    file: UploadFile = File(...),
    description: str | None = Form(None),
    organization_id: str | None = Form(None),
    project_id: str | None = Form(None),
    created_by: str | None = Form(None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    meeting = Meeting(
        title=title,
        description=description,
        organization_id=organization_id,
        project_id=project_id,
        created_by=created_by,
        source_type=_source_type(file.filename),
        status=MeetingStatus.UPLOADED,
    )
    db.add(meeting)
    db.flush()
    try:
        asset = await ingest_media(db, meeting.id, file, _storage())
        return _success({"meeting": _meeting_data(meeting), "media": _media_data(asset)}, 201)
    except MediaValidationError as exc:
        db.rollback()
        return _error(exc.code, exc.message, exc.status_code)


@router.post("/meetings/{meeting_id}/media", status_code=201, summary="Upload media to an existing meeting")
async def upload_media(
    meeting_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> JSONResponse:
    try:
        asset = await ingest_media(db, meeting_id, file, _storage())
        return _success(_media_data(asset), 201)
    except MediaValidationError as exc:
        return _error(exc.code, exc.message, exc.status_code)


@router.get("/meetings/{meeting_id}/media", summary="List meeting media")
def get_meeting_media(meeting_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    return _success([_media_data(asset) for asset in list_media(db, meeting_id)])


@router.get("/media/{media_id}", summary="Get media metadata")
def get_media_metadata(media_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    asset = get_media(db, media_id)
    if asset is None:
        return _error("MEDIA_NOT_FOUND", "Media not found", 404)
    return _success(_media_data(asset))


@router.get("/media/{media_id}/stream", summary="Stream media for synchronized playback")
def stream_media(media_id: str, db: Session = Depends(get_db)):
    asset = get_media(db, media_id)
    if asset is None:
        return _error("MEDIA_NOT_FOUND", "Media not found", 404)
    with tempfile.NamedTemporaryFile(prefix="meeting-playback-", suffix=Path(asset.filename).suffix, delete=False) as handle:
        temporary_path = Path(handle.name)
    temporary_path.unlink(missing_ok=True)
    try:
        create_storage_provider().materialize(asset.storage_key, temporary_path)
    except (StorageError, OSError):
        temporary_path.unlink(missing_ok=True)
        return _error("MEDIA_UNAVAILABLE", "Media is unavailable", 404)

    def chunks() -> Iterator[bytes]:
        try:
            with temporary_path.open("rb") as media_file:
                while chunk := media_file.read(1_048_576):
                    yield chunk
        finally:
            temporary_path.unlink(missing_ok=True)

    return StreamingResponse(chunks(), media_type=asset.mime_type)


@router.delete("/media/{media_id}", summary="Delete media and its storage object")
def remove_media(media_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    try:
        deleted = delete_media(db, media_id, _storage())
    except MediaValidationError as exc:
        return _error(exc.code, exc.message, exc.status_code)
    if not deleted:
        return _error("MEDIA_NOT_FOUND", "Media not found", 404)
    return _success({"deleted": True})
