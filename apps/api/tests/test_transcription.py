from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.transcription import router
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import (
    MediaAsset,
    Meeting,
    MeetingStatus,
    SourceType,
    Transcript,
    TranscriptSegment,
    TranscriptionStatus,
)
from app.services.asr import ASRProvider, ASRProviderError, ASRSegmentResult, TranscriptResult
from app.services.jobs import JobStatus
from app.services.media_audio import audio_path_for_asset
from app.services.storage import LocalStorageProvider
from app.services.transcript_service import TranscriptionError, transcribe_media_asset


class DeterministicFakeASRProvider(ASRProvider):
    name = "test-fake"
    model_name = "deterministic"
    requested_device = "cpu"
    compute_type = "int8"
    language = "en"
    beam_size = 1
    vad_filter = False

    def __init__(self, result: TranscriptResult | None = None, failure: Exception | None = None) -> None:
        self.result = result
        self.failure = failure
        self.calls = 0
        self.last_audio_path: Path | None = None

    def transcribe(self, media_asset: MediaAsset, audio_path: Path) -> TranscriptResult:
        self.calls += 1
        self.last_audio_path = audio_path
        if self.failure:
            raise self.failure
        assert self.result is not None
        return self.result


@pytest.fixture(scope="session")
def asr_fixtures(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.fail("ffmpeg is required for ASR fixtures")
    directory = tmp_path_factory.mktemp("asr-fixtures")
    audio = directory / "speech.wav"
    video = directory / "speech.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=660:duration=0.3", "-c:a", "pcm_s16le", str(audio)],
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
            "color=c=black:s=64x48:d=0.3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=0.3",
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


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'transcription.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session, tmp_path / "storage"
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def create_asset(session: Session, storage: LocalStorageProvider, meeting: Meeting, source: Path, mime: str) -> MediaAsset:
    storage_key = f"meetings/{meeting.id}/{source.name}"
    storage.put_file(source, storage_key)
    asset = MediaAsset(
        meeting_id=meeting.id,
        filename=source.name,
        original_filename=source.name,
        mime_type=mime,
        size_bytes=source.stat().st_size,
        duration_seconds=0.3,
        codec="pcm_s16le" if source.suffix == ".wav" else "h264",
        sample_rate=44100,
        channels=1,
        checksum="fixture",
        storage_key=storage_key,
    )
    session.add(asset)
    session.commit()
    return asset


def successful_result() -> TranscriptResult:
    segments = [
        ASRSegmentResult(0.0, 3.2, "Hello everyone. ", "en", 0.98),
        ASRSegmentResult(7.0, 10.4, "Today we are discussing the deployment schedule.", "en", 0.95),
    ]
    return TranscriptResult(
        segments=segments,
        text="Hello everyone. Today we are discussing the deployment schedule.",
        provider="test-fake",
        model="deterministic",
        device="cpu",
        language="en",
        processing_duration=0.01,
    )


def test_asr_provider_interface_and_timestamp_preservation(
    database, asr_fixtures: dict[str, Path]
) -> None:
    session, storage_root = database
    storage = LocalStorageProvider(storage_root)
    meeting = Meeting(title="ASR test", source_type=SourceType.AUDIO, status=MeetingStatus.UPLOADED)
    session.add(meeting)
    session.flush()
    asset = create_asset(session, storage, meeting, asr_fixtures["audio"], "audio/wav")
    provider = DeterministicFakeASRProvider(successful_result())

    state = transcribe_media_asset(session, asset.id, provider, storage)

    assert isinstance(provider, ASRProvider)
    assert [segment.start_time for segment in state.segments] == [0.0, 7.0]
    assert [segment.sequence for segment in state.segments] == [0, 1]
    assert state.transcript.text == "Hello everyone. Today we are discussing the deployment schedule."
    assert state.segments[0].text == "Hello everyone. "
    assert state.segments[0].language == "en"


def test_transcript_persistence_ordering_and_idempotency(
    database, asr_fixtures: dict[str, Path]
) -> None:
    session, storage_root = database
    storage = LocalStorageProvider(storage_root)
    meeting = Meeting(title="Idempotency", source_type=SourceType.AUDIO, status=MeetingStatus.UPLOADED)
    session.add(meeting)
    session.flush()
    asset = create_asset(session, storage, meeting, asr_fixtures["audio"], "audio/wav")
    provider = DeterministicFakeASRProvider(successful_result())

    first = transcribe_media_asset(session, asset.id, provider, storage)
    second = transcribe_media_asset(session, asset.id, provider, storage)

    assert second.reused is True
    assert provider.calls == 1
    assert session.scalar(select(func.count(Transcript.id))) == 1
    assert session.scalar(select(func.count(TranscriptSegment.id))) == 2
    assert first.transcript.status == TranscriptionStatus.TRANSCRIBED


def test_video_audio_extraction(database, asr_fixtures: dict[str, Path]) -> None:
    session, storage_root = database
    storage = LocalStorageProvider(storage_root)
    meeting = Meeting(title="Video ASR", source_type=SourceType.VIDEO, status=MeetingStatus.UPLOADED)
    session.add(meeting)
    session.flush()
    asset = create_asset(session, storage, meeting, asr_fixtures["video"], "video/mp4")
    provider = DeterministicFakeASRProvider(successful_result())

    with audio_path_for_asset(asset, storage, "ffmpeg") as audio_path:
        assert audio_path.suffix == ".wav"
        assert audio_path.exists()
        provider.transcribe(asset, audio_path)
    assert provider.last_audio_path is not None


def test_missing_media_and_empty_transcript(database) -> None:
    session, storage_root = database
    storage = LocalStorageProvider(storage_root)
    provider = DeterministicFakeASRProvider(successful_result())

    with pytest.raises(TranscriptionError, match="Media asset not found"):
        transcribe_media_asset(session, "missing", provider, storage)

    meeting = Meeting(title="Empty", source_type=SourceType.AUDIO, status=MeetingStatus.UPLOADED)
    session.add(meeting)
    session.commit()
    asset = MediaAsset(
        meeting_id=meeting.id,
        filename="empty.wav",
        original_filename="empty.wav",
        mime_type="audio/wav",
        size_bytes=1,
        checksum="empty",
        storage_key="empty.wav",
    )
    session.add(asset)
    session.commit()
    (storage_root / "empty.wav").parent.mkdir(parents=True, exist_ok=True)
    (storage_root / "empty.wav").write_bytes(b"not-audio")
    empty_provider = DeterministicFakeASRProvider(
        TranscriptResult([], "", "test-fake", "deterministic", "cpu", "en", 0.01)
    )
    with pytest.raises(TranscriptionError, match="not readable audio"):
        transcribe_media_asset(session, asset.id, empty_provider, storage)


def test_asr_failure_is_retryable(database, asr_fixtures: dict[str, Path]) -> None:
    session, storage_root = database
    storage = LocalStorageProvider(storage_root)
    meeting = Meeting(title="Retry", source_type=SourceType.AUDIO, status=MeetingStatus.UPLOADED)
    session.add(meeting)
    session.flush()
    asset = create_asset(session, storage, meeting, asr_fixtures["audio"], "audio/wav")
    failing = DeterministicFakeASRProvider(failure=ASRProviderError("model failed"))

    with pytest.raises(TranscriptionError, match="model failed"):
        transcribe_media_asset(session, asset.id, failing, storage)
    failed = session.scalar(select(Transcript).where(Transcript.meeting_id == meeting.id))
    assert failed is not None
    assert failed.status == TranscriptionStatus.TRANSCRIPTION_FAILED

    retry = transcribe_media_asset(session, asset.id, DeterministicFakeASRProvider(successful_result()), storage)
    assert retry.transcript.status == TranscriptionStatus.TRANSCRIBED
    assert session.scalar(select(func.count(Transcript.id))) == 1


def test_transcription_api_endpoint(database, asr_fixtures: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    session, storage_root = database
    storage = LocalStorageProvider(storage_root)
    meeting = Meeting(title="API transcription", source_type=SourceType.AUDIO, status=MeetingStatus.UPLOADED)
    session.add(meeting)
    session.flush()
    asset = create_asset(session, storage, meeting, asr_fixtures["audio"], "audio/wav")

    import app.api.transcription as transcription_api

    monkeypatch.setattr(
        transcription_api,
        "enqueue_transcription_job",
        lambda meeting_id, media_asset_id: JobStatus("job-1", meeting_id, TranscriptionStatus.TRANSCRIPTION_QUEUED),
    )
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app) as client:
            queued = client.post(f"/api/meetings/{meeting.id}/transcribe")
            assert queued.status_code == 202
            assert queued.json()["data"]["job_id"] == "job-1"

            state = transcribe_media_asset(session, asset.id, DeterministicFakeASRProvider(successful_result()), storage)
            transcript = client.get(f"/api/meetings/{meeting.id}/transcript")
            assert transcript.status_code == 200
            assert len(transcript.json()["data"]["segments"]) == 2
            segment = client.get(f"/api/meetings/{meeting.id}/transcript/{state.segments[0].id}")
            assert segment.status_code == 200
            assert segment.json()["data"]["start_time"] == 0.0
    finally:
        app.dependency_overrides.clear()
