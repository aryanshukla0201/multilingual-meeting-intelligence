from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import (
    DiarizationRun,
    DiarizationStatus,
    MediaAsset,
    Meeting,
    MeetingStatus,
    SourceType,
    Speaker,
    SpeakerSegment,
    Transcript,
    TranscriptSegment,
)
from app.services.diarization import (
    DiarizationProvider,
    DiarizationProviderError,
    DiarizationResult,
    DiarizationSegmentResult,
    validate_diarization_result,
)
from app.services.jobs import JobStatus
from app.services.speaker_service import SpeakerProcessingError, diarize_media_asset
from app.services.storage import LocalStorageProvider


class DeterministicFakeDiarizationProvider(DiarizationProvider):
    name = "test-fake-diarization"
    model_name = "deterministic"
    requested_device = "cpu"
    min_speakers = None
    max_speakers = None

    def __init__(self, segments: list[DiarizationSegmentResult], failure: Exception | None = None) -> None:
        self.segments = segments
        self.failure = failure
        self.calls = 0

    def diarize(self, media_asset: MediaAsset, audio_path: Path) -> DiarizationResult:
        self.calls += 1
        if self.failure:
            raise self.failure
        return DiarizationResult(self.segments, self.name, self.model_name, "cpu", 0.01)


@pytest.fixture(scope="session")
def speaker_audio(tmp_path_factory: pytest.TempPathFactory) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.fail("ffmpeg is required for diarization fixtures")
    path = tmp_path_factory.mktemp("speaker-fixtures") / "speaker.wav"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=500:duration=0.4", "-c:a", "pcm_s16le", str(path)],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'speaker.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session, tmp_path / "storage"
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def create_asset(session: Session, storage: LocalStorageProvider, source: Path) -> tuple[Meeting, MediaAsset]:
    meeting = Meeting(title="Speaker test", source_type=SourceType.AUDIO, status=MeetingStatus.UPLOADED)
    session.add(meeting)
    session.flush()
    key = f"meetings/{meeting.id}/{source.name}"
    storage.put_file(source, key)
    asset = MediaAsset(
        meeting_id=meeting.id,
        filename=source.name,
        original_filename=source.name,
        mime_type="audio/wav",
        size_bytes=source.stat().st_size,
        duration_seconds=0.4,
        codec="pcm_s16le",
        sample_rate=44100,
        channels=1,
        checksum="speaker-fixture",
        storage_key=key,
    )
    session.add(asset)
    session.commit()
    return meeting, asset


def add_transcript(session: Session, meeting: Meeting, asset: MediaAsset) -> None:
    transcript = Transcript(
        meeting_id=meeting.id,
        media_asset_id=asset.id,
        text="Hello everyone. Let's begin.",
        provider="test-fake",
        provider_model="deterministic",
        status="TRANSCRIBED",
    )
    session.add(transcript)
    session.flush()
    session.add_all(
        [
            TranscriptSegment(
                transcript_id=transcript.id,
                meeting_id=meeting.id,
                media_asset_id=asset.id,
                segment_index=0,
                sequence=0,
                start_time=0,
                end_time=5,
                text="Hello everyone.",
            ),
            TranscriptSegment(
                transcript_id=transcript.id,
                meeting_id=meeting.id,
                media_asset_id=asset.id,
                segment_index=1,
                sequence=1,
                start_time=6,
                end_time=10,
                text="Let's begin.",
            ),
        ]
    )
    session.commit()


def test_provider_interface_and_timestamp_validation() -> None:
    provider = DeterministicFakeDiarizationProvider(
        [DiarizationSegmentResult("SPEAKER_00", 0, 5, 0.9)]
    )
    assert isinstance(provider, DiarizationProvider)
    validate_diarization_result(DiarizationResult(provider.segments, "fake", "model", "cpu", 0.1))
    with pytest.raises(DiarizationProviderError, match="invalid timestamps"):
        validate_diarization_result(
            DiarizationResult([DiarizationSegmentResult("SPEAKER_00", 5, 5)], "fake", "model", "cpu", 0.1)
        )


def test_alignment_critical_case_and_persistence(database, speaker_audio: Path) -> None:
    session, storage_root = database
    storage = LocalStorageProvider(storage_root)
    meeting, asset = create_asset(session, storage, speaker_audio)
    add_transcript(session, meeting, asset)
    provider = DeterministicFakeDiarizationProvider(
        [
            DiarizationSegmentResult("SPEAKER_00", 0, 5, 0.9),
            DiarizationSegmentResult("SPEAKER_01", 6, 10, 0.8),
        ]
    )

    state = diarize_media_asset(session, asset.id, provider, storage)

    assert [speaker.label for speaker in state.speakers] == ["SPEAKER_00", "SPEAKER_01"]
    transcript_segments = list(
        session.scalars(
            select(TranscriptSegment).order_by(TranscriptSegment.sequence)
        ).all()
    )
    labels = {speaker.id: speaker.label for speaker in state.speakers}
    assert [labels[segment.speaker_id] for segment in transcript_segments] == ["SPEAKER_00", "SPEAKER_01"]
    assert session.scalar(select(func.count(SpeakerSegment.id))) == 2
    assert state.run.status == DiarizationStatus.DIARIZATION_COMPLETED


def test_overlapping_raw_segments_are_preserved(database, speaker_audio: Path) -> None:
    session, storage_root = database
    storage = LocalStorageProvider(storage_root)
    meeting, asset = create_asset(session, storage, speaker_audio)
    provider = DeterministicFakeDiarizationProvider(
        [
            DiarizationSegmentResult("SPEAKER_00", 10, 15, 0.7),
            DiarizationSegmentResult("SPEAKER_01", 13, 17, 0.8),
        ]
    )

    state = diarize_media_asset(session, asset.id, provider, storage)

    assert len(state.segments) == 2
    assert {(segment.start_time, segment.end_time) for segment in state.segments} == {(10, 15), (13, 17)}


def test_missing_media_and_empty_result(database, speaker_audio: Path) -> None:
    session, storage_root = database
    storage = LocalStorageProvider(storage_root)
    provider = DeterministicFakeDiarizationProvider([])

    with pytest.raises(SpeakerProcessingError, match="Media asset not found"):
        diarize_media_asset(session, "missing", provider, storage)

    _, asset = create_asset(session, storage, speaker_audio)
    with pytest.raises(SpeakerProcessingError, match="no speaker segments"):
        diarize_media_asset(session, asset.id, provider, storage)


def test_idempotency_and_retry(database, speaker_audio: Path) -> None:
    session, storage_root = database
    storage = LocalStorageProvider(storage_root)
    meeting, asset = create_asset(session, storage, speaker_audio)
    provider = DeterministicFakeDiarizationProvider(
        [DiarizationSegmentResult("SPEAKER_00", 0, 5, 0.9)]
    )

    first = diarize_media_asset(session, asset.id, provider, storage)
    second = diarize_media_asset(session, asset.id, provider, storage)
    assert second.reused is True
    assert provider.calls == 1
    assert session.scalar(select(func.count(SpeakerSegment.id))) == 1

    failing = DeterministicFakeDiarizationProvider([], DiarizationProviderError("provider failed"))
    meeting2, asset2 = create_asset(session, storage, speaker_audio)
    with pytest.raises(SpeakerProcessingError, match="provider failed"):
        diarize_media_asset(session, asset2.id, failing, storage)
    failed_run = session.scalar(select(DiarizationRun).where(DiarizationRun.media_asset_id == asset2.id))
    assert failed_run.status == DiarizationStatus.DIARIZATION_FAILED
    retried = diarize_media_asset(session, asset2.id, provider, storage)
    assert retried.run.status == DiarizationStatus.DIARIZATION_COMPLETED


def test_mapping_and_speaker_api(database, speaker_audio: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session, storage_root = database
    storage = LocalStorageProvider(storage_root)
    meeting, asset = create_asset(session, storage, speaker_audio)
    provider = DeterministicFakeDiarizationProvider(
        [DiarizationSegmentResult("SPEAKER_00", 0, 5, 0.9)]
    )
    diarize_media_asset(session, asset.id, provider, storage)

    import app.api.speakers as speaker_api

    monkeypatch.setattr(
        speaker_api,
        "enqueue_diarization_job",
        lambda meeting_id, media_asset_id: JobStatus("speaker-job", meeting_id, "DIARIZATION_QUEUED"),
    )
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app) as client:
            speakers = client.get(f"/api/meetings/{meeting.id}/speakers")
            assert speakers.status_code == 200
            assert speakers.json()["data"][0]["label"] == "SPEAKER_00"
            updated = client.patch(
                f"/api/meetings/{meeting.id}/speakers/SPEAKER_00",
                json={"display_name": "Rahul"},
            )
            assert updated.status_code == 200
            assert updated.json()["data"]["display_name"] == "Rahul"
            segments = client.get(f"/api/meetings/{meeting.id}/speaker-segments")
            assert segments.json()["data"][0]["speaker_display_name"] == "Rahul"
            queued = client.post(f"/api/meetings/{meeting.id}/diarize")
            assert queued.status_code == 202
    finally:
        app.dependency_overrides.clear()
