from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import (
    Event,
    EventEvidence,
    EventExtractionRun,
    EventExtractionStatus,
    EventType,
    Meeting,
    MeetingStatus,
    Speaker,
    SourceType,
    Transcript,
    TranscriptSegment,
)
from app.services.event_extraction import (
    EventExtractionError,
    EventExtractionProvider,
    ExtractedEvent,
    TranscriptExtractionInput,
)
from app.services.event_service import EventServiceError, extract_meeting_events
from app.services.jobs import JobStatus


class DeterministicFakeEventProvider(EventExtractionProvider):
    name = "test-fake-events"
    model_name = "deterministic"
    temperature = 0.0

    def __init__(self, events: list[ExtractedEvent], failure: Exception | None = None) -> None:
        self.events = events
        self.failure = failure
        self.calls = 0

    def extract_events(
        self,
        transcript_segments: list[TranscriptExtractionInput],
        meeting_context: str | None = None,
    ) -> list[ExtractedEvent]:
        self.calls += 1
        if self.failure:
            raise self.failure
        return self.events


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'events.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def create_meeting_with_transcript(session: Session) -> tuple[Meeting, list[TranscriptSegment]]:
    meeting = Meeting(title="Deployment discussion", source_type=SourceType.AUDIO, status=MeetingStatus.UPLOADED)
    session.add(meeting)
    session.flush()
    speaker_a = Speaker(meeting_id=meeting.id, label="SPEAKER_00")
    speaker_b = Speaker(meeting_id=meeting.id, label="SPEAKER_01")
    session.add_all([speaker_a, speaker_b])
    session.flush()
    transcript = Transcript(meeting_id=meeting.id, status="TRANSCRIBED", text="fixture")
    session.add(transcript)
    session.flush()
    segments = [
        TranscriptSegment(
            transcript_id=transcript.id,
            meeting_id=meeting.id,
            segment_index=0,
            sequence=0,
            start_time=0,
            end_time=5,
            text="Let's discuss the deployment.",
            speaker_id=speaker_a.id,
        ),
        TranscriptSegment(
            transcript_id=transcript.id,
            meeting_id=meeting.id,
            segment_index=1,
            sequence=1,
            start_time=6,
            end_time=11,
            text="I'll deploy the new API this Friday.",
            speaker_id=speaker_a.id,
        ),
        TranscriptSegment(
            transcript_id=transcript.id,
            meeting_id=meeting.id,
            segment_index=2,
            sequence=2,
            start_time=12,
            end_time=17,
            text="That sounds risky because the security review isn't complete.",
            speaker_id=speaker_b.id,
        ),
        TranscriptSegment(
            transcript_id=transcript.id,
            meeting_id=meeting.id,
            segment_index=3,
            sequence=3,
            start_time=18,
            end_time=23,
            text="Could we instead deploy next Monday?",
            speaker_id=speaker_b.id,
        ),
    ]
    session.add_all(segments)
    session.commit()
    return meeting, segments


def critical_events(segments: list[TranscriptSegment]) -> list[ExtractedEvent]:
    return [
        ExtractedEvent(
            event_type=EventType.COMMITMENT,
            title="Deploy API",
            subject="API deployment",
            value="Friday",
            transcript_segment_ids=[segments[1].id],
            confidence=0.94,
        ),
        ExtractedEvent(
            event_type=EventType.RISK,
            title="Security review incomplete",
            subject="Security review",
            value="Incomplete",
            transcript_segment_ids=[segments[2].id],
            confidence=0.88,
        ),
        ExtractedEvent(
            event_type=EventType.PROPOSAL,
            title="Deploy next Monday",
            subject="API deployment",
            value="Next Monday",
            transcript_segment_ids=[segments[3].id],
            confidence=0.81,
        ),
    ]


def test_event_persistence_evidence_speakers_and_derived_time(database) -> None:
    meeting, segments = create_meeting_with_transcript(database)
    state = extract_meeting_events(database, meeting.id, DeterministicFakeEventProvider(critical_events(segments)))

    assert len(state.events) == 3
    commitment = next(event for event in state.events if event.event_type == EventType.COMMITMENT)
    assert commitment.speaker_id == segments[1].speaker_id
    assert commitment.start_time == 6
    assert commitment.end_time == 11
    evidence = database.scalars(select(EventEvidence).where(EventEvidence.event_id == commitment.id)).all()
    assert len(evidence) == 1
    assert evidence[0].transcript_segment_id == segments[1].id


def test_multi_segment_event_and_no_events_result(database) -> None:
    meeting, segments = create_meeting_with_transcript(database)
    provider = DeterministicFakeEventProvider(
        [
            ExtractedEvent(
                event_type=EventType.CLAIM,
                title="Deployment discussion",
                subject="Deployment",
                value="Discussed",
                transcript_segment_ids=[segments[0].id, segments[1].id],
                confidence=0.7,
            )
        ]
    )
    state = extract_meeting_events(database, meeting.id, provider)
    assert state.events[0].start_time == 0
    assert state.events[0].end_time == 11
    assert database.scalar(select(func.count(EventEvidence.id)).where(EventEvidence.event_id == state.events[0].id)) == 2

    empty_meeting = Meeting(title="Empty", source_type=SourceType.AUDIO, status=MeetingStatus.UPLOADED)
    database.add(empty_meeting)
    database.commit()
    empty_state = extract_meeting_events(database, empty_meeting.id, DeterministicFakeEventProvider([]))
    assert empty_state.events == []
    assert empty_state.run.status == EventExtractionStatus.COMPLETED


def test_invalid_evidence_cross_meeting_and_validation(database) -> None:
    meeting, segments = create_meeting_with_transcript(database)
    invalid = ExtractedEvent(
        event_type=EventType.ACTION,
        title="Invalid evidence",
        transcript_segment_ids=["does-not-exist"],
        confidence=0.5,
    )
    with pytest.raises(EventServiceError, match="outside this meeting"):
        extract_meeting_events(database, meeting.id, DeterministicFakeEventProvider([invalid]))
    assert database.scalar(select(func.count(Event.id))) == 0
    run = database.scalar(select(EventExtractionRun).where(EventExtractionRun.meeting_id == meeting.id))
    assert run.status == EventExtractionStatus.FAILED

    other_meeting, other_segments = create_meeting_with_transcript(database)
    cross_meeting_event = ExtractedEvent(
        event_type=EventType.CLAIM,
        title="Cross meeting evidence",
        transcript_segment_ids=[other_segments[0].id],
        confidence=0.5,
    )
    with pytest.raises(EventServiceError, match="outside this meeting"):
        extract_meeting_events(database, meeting.id, DeterministicFakeEventProvider([cross_meeting_event]))

    with pytest.raises(ValidationError):
        ExtractedEvent(
            event_type="NOT_ALLOWED",
            title="Bad",
            transcript_segment_ids=[segments[0].id],
            confidence=0.5,
        )
    with pytest.raises(ValidationError):
        ExtractedEvent(
            event_type=EventType.ACTION,
            title="Bad confidence",
            transcript_segment_ids=[segments[0].id],
            confidence=1.1,
        )


def test_idempotency_and_failed_retry(database) -> None:
    meeting, segments = create_meeting_with_transcript(database)
    provider = DeterministicFakeEventProvider(critical_events(segments))
    first = extract_meeting_events(database, meeting.id, provider)
    second = extract_meeting_events(database, meeting.id, provider)
    assert second.reused is True
    assert provider.calls == 1
    assert database.scalar(select(func.count(Event.id))) == 3

    retry_meeting, retry_segments = create_meeting_with_transcript(database)
    failing = DeterministicFakeEventProvider([], EventExtractionError("provider unavailable"))
    with pytest.raises(EventServiceError, match="provider unavailable"):
        extract_meeting_events(database, retry_meeting.id, failing)
    retry = extract_meeting_events(database, retry_meeting.id, DeterministicFakeEventProvider(critical_events(retry_segments)))
    assert retry.run.status == EventExtractionStatus.COMPLETED


def test_event_api_filters_detail_and_status(database, monkeypatch: pytest.MonkeyPatch) -> None:
    meeting, segments = create_meeting_with_transcript(database)
    provider = DeterministicFakeEventProvider(critical_events(segments))
    extract_meeting_events(database, meeting.id, provider)

    import app.api.events as events_api

    monkeypatch.setattr(
        events_api,
        "create_event_extraction_provider",
        lambda: provider,
    )
    monkeypatch.setattr(
        events_api,
        "enqueue_event_extraction_job",
        lambda meeting_id: JobStatus("event-job", meeting_id, "QUEUED"),
    )
    app.dependency_overrides[get_db] = lambda: database
    try:
        with TestClient(app) as client:
            events = client.get(f"/api/meetings/{meeting.id}/events?event_type=RISK")
            assert events.status_code == 200
            assert len(events.json()["data"]) == 1
            event_id = events.json()["data"][0]["id"]
            detail = client.get(f"/api/meetings/{meeting.id}/events/{event_id}")
            assert detail.status_code == 200
            assert detail.json()["data"]["evidence"][0]["transcript_segment_id"] == segments[2].id
            status = client.get(f"/api/meetings/{meeting.id}/events/extraction/status")
            assert status.status_code == 200
            assert status.json()["data"]["event_count"] == 3
            queued = client.post(f"/api/meetings/{meeting.id}/events/extract")
            assert queued.status_code == 200
            assert queued.json()["data"]["status"] == "COMPLETED"
    finally:
        app.dependency_overrides.clear()


def test_worker_dispatches_event_job(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.worker as worker

    class DummySession:
        def close(self) -> None:
            pass

    statuses: list[str] = []
    monkeypatch.setattr(worker, "SessionLocal", lambda: DummySession())
    monkeypatch.setattr(worker, "update_job", lambda job_id, meeting_id, status, error=None: statuses.append(
        status.value if hasattr(status, "value") else status
    ))
    monkeypatch.setattr(worker, "create_event_extraction_provider", lambda: object())
    monkeypatch.setattr(worker, "extract_meeting_events", lambda db, meeting_id, provider: None)

    worker.process_transcription_job({"kind": "event_extraction", "job_id": "job", "meeting_id": "meeting"})

    assert statuses == ["PROCESSING", "COMPLETED"]
