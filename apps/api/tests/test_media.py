from __future__ import annotations

import hashlib
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.media import router
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import Meeting, MeetingStatus, SourceType
from app.services.media_inspection import (
    MediaValidationError,
    inspect_media,
    validate_extension_and_mime,
)
from app.services.media_service import _stage_upload, ingest_media
from app.services.storage import LocalStorageProvider, StorageError
from app.settings import settings


@pytest.fixture(scope="session")
def media_fixtures(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.fail("ffmpeg is required for deterministic media fixtures")
    directory = tmp_path_factory.mktemp("media-fixtures")
    audio = directory / "fixture.wav"
    video = directory / "fixture.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=0.25", "-c:a", "pcm_s16le", str(audio)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x48:d=0.25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.25",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    return {"audio": audio, "video": video}


def upload_from_path(path: Path, content_type: str) -> UploadFile:
    return UploadFile(file=path.open("rb"), filename=path.name, headers={"content-type": content_type})


def test_valid_audio_metadata(media_fixtures: dict[str, Path]) -> None:
    metadata = inspect_media(media_fixtures["audio"], ".wav", settings.ffprobe_path)

    assert metadata.duration_seconds is not None
    assert metadata.duration_seconds > 0
    assert metadata.codec == "pcm_s16le"
    assert metadata.sample_rate == 44100
    assert metadata.channels == 1


def test_valid_video_metadata(media_fixtures: dict[str, Path]) -> None:
    metadata = inspect_media(media_fixtures["video"], ".mp4", settings.ffprobe_path)

    assert metadata.duration_seconds is not None
    assert metadata.width == 64
    assert metadata.height == 48
    assert metadata.codec == "h264"
    assert metadata.fps is not None


def test_unsupported_extension() -> None:
    with pytest.raises(MediaValidationError, match="Unsupported media extension"):
        validate_extension_and_mime("meeting.pdf", "application/pdf")


def test_invalid_mime_type() -> None:
    with pytest.raises(MediaValidationError, match="MIME type does not match"):
        validate_extension_and_mime("meeting.wav", "video/mp4")


@pytest.mark.anyio
async def test_checksum_generation(tmp_path: Path) -> None:
    content = b"deterministic media bytes"
    upload = UploadFile(file=BytesIO(content), filename="sample.wav", headers={"content-type": "audio/wav"})
    staged_path, size, checksum = await _stage_upload(upload)
    try:
        assert size == len(content)
        assert checksum == hashlib.sha256(content).hexdigest()
        assert staged_path.read_bytes() == content
    finally:
        staged_path.unlink(missing_ok=True)


@pytest.mark.anyio
async def test_oversized_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_media_size_bytes", 3)
    upload = UploadFile(file=BytesIO(b"1234"), filename="sample.wav", headers={"content-type": "audio/wav"})

    with pytest.raises(MediaValidationError, match="exceeds the configured size limit") as error:
        await _stage_upload(upload)
    assert error.value.status_code == 413


def test_path_traversal_protection(tmp_path: Path) -> None:
    provider = LocalStorageProvider(tmp_path)
    source = tmp_path / "source.bin"
    source.write_bytes(b"data")

    with pytest.raises(StorageError):
        provider.put_file(source, "../outside.bin")
    assert not (tmp_path.parent / "outside.bin").exists()


def test_local_storage_provider(tmp_path: Path) -> None:
    provider = LocalStorageProvider(tmp_path / "storage")
    source = tmp_path / "source.bin"
    source.write_bytes(b"stored")

    provider.put_file(source, "meeting/media.bin")
    assert provider.exists("meeting/media.bin")
    provider.delete("meeting/media.bin")
    assert not provider.exists("meeting/media.bin")


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'media.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session, tmp_path / "storage"
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def test_upload_storage_metadata_database_persistence(
    database, media_fixtures: dict[str, Path]
) -> None:
    session, storage_root = database
    meeting = Meeting(title="Media test", source_type=SourceType.AUDIO, status=MeetingStatus.UPLOADED)
    session.add(meeting)
    session.flush()
    provider = LocalStorageProvider(storage_root)

    import asyncio

    asset = asyncio.run(ingest_media(session, meeting.id, upload_from_path(media_fixtures["audio"], "audio/wav"), provider))

    assert asset.id
    assert asset.meeting_id == meeting.id
    assert asset.size_bytes == media_fixtures["audio"].stat().st_size
    assert asset.duration_seconds and asset.duration_seconds > 0
    assert provider.exists(asset.storage_key)
    assert session.get(type(asset), asset.id) is not None


def test_api_upload_create_and_list(database, media_fixtures: dict[str, Path]) -> None:
    session, storage_root = database
    provider = LocalStorageProvider(storage_root)
    original_factory = __import__("app.api.media", fromlist=["_storage"])._storage
    import app.api.media as media_api

    app.dependency_overrides[get_db] = lambda: session
    media_api._storage = lambda: provider
    try:
        with TestClient(app) as client:
            with media_fixtures["video"].open("rb") as media_file:
                response = client.post(
                    "/api/meetings",
                    data={"title": "API media test"},
                    files={"file": ("clip.mp4", media_file, "video/mp4")},
                )
            assert response.status_code == 201
            payload = response.json()
            assert payload["success"] is True
            meeting_id = payload["data"]["meeting"]["id"]
            media_id = payload["data"]["media"]["id"]

            listed = client.get(f"/api/meetings/{meeting_id}/media")
            assert listed.status_code == 200
            assert listed.json()["data"][0]["id"] == media_id

            metadata = client.get(f"/api/media/{media_id}")
            assert metadata.status_code == 200
            assert metadata.json()["data"]["width"] == 64
    finally:
        media_api._storage = original_factory
        app.dependency_overrides.clear()
