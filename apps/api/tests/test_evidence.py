from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import (
    Evidence,
    EvidenceType,
    Event,
    EventExtractionRun,
    EventExtractionStatus,
    Event,
    EventEvidence,
    Meeting,
    MeetingIntelligence,
    MeetingIntelligenceRun,
    MeetingIntelligenceStatus,
    MeetingStatus,
    SourceType,
    Speaker,
    Transcript,
    TranscriptSegment,
)
from app.services.evidence_service import (
    EvidenceServiceError,
    create_evidence,
    get_evidence,
    get_evidence_for_event,
    list_meeting_evidence,
)


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'evidence.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def _meeting_with_segments(session: Session) -> tuple[Meeting, list[TranscriptSegment], Speaker, Speaker]:
    meeting = Meeting(title="Evidence test", source_type=SourceType.AUDIO, status=MeetingStatus.UPLOADED)
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
            text="We will ship the API on Friday.",
            speaker_id=speaker_a.id,
        ),
        TranscriptSegment(
            transcript_id=transcript.id,
            meeting_id=meeting.id,
            segment_index=1,
            sequence=1,
            start_time=6,
            end_time=11,
            text="The security review is still incomplete.",
            speaker_id=speaker_b.id,
        ),
    ]
    session.add_all(segments)
    session.commit()
    return meeting, segments, speaker_a, speaker_b


def test_create_and_retrieve_transcript_evidence(database: Session) -> None:
    meeting, segments, speaker_a, _ = _meeting_with_segments(database)

    evidence = create_evidence(
        database,
        meeting_id=meeting.id,
        evidence_type=EvidenceType.TRANSCRIPT,
        transcript_segment_id=segments[0].id,
        speaker_id=speaker_a.id,
        media_asset_id=None,
        start_time=0.0,
        end_time=5.0,
        content=segments[0].text,
        confidence=0.97,
        metadata={"segment_index": segments[0].segment_index},
    )

    assert evidence.meeting_id == meeting.id
    assert evidence.transcript_segment_id == segments[0].id
    assert get_evidence(database, meeting.id, evidence.id).id == evidence.id
    assert list_meeting_evidence(database, meeting.id)[0].id == evidence.id


def test_event_and_intelligence_chain(database: Session) -> None:
    meeting, segments, speaker_a, _ = _meeting_with_segments(database)

    extraction_run = EventExtractionRun(
        meeting_id=meeting.id,
        provider="test-provider",
        configuration_key="config-1",
        status=EventExtractionStatus.COMPLETED,
    )
    database.add(extraction_run)
    database.flush()

    event = Event(
        meeting_id=meeting.id,
        extraction_run_id=extraction_run.id,
        speaker_id=speaker_a.id,
        event_type="COMMITMENT",
        title="Ship API Friday",
        original_text=segments[0].text,
        start_time=segments[0].start_time,
        end_time=segments[0].end_time,
        confidence_score=0.98,
    )
    database.add(event)
    database.flush()

    evidence = create_evidence(
        database,
        meeting_id=meeting.id,
        event_id=event.id,
        evidence_type=EvidenceType.TRANSCRIPT,
        transcript_segment_id=segments[0].id,
        speaker_id=speaker_a.id,
        start_time=segments[0].start_time,
        end_time=segments[0].end_time,
        content=segments[0].text,
    )
    event_evidence = EventEvidence(
        event_id=event.id,
        evidence_id=evidence.id,
        transcript_segment_id=segments[0].id,
        evidence_start=segments[0].start_time,
        evidence_end=segments[0].end_time,
        relevance=0.98,
    )
    database.add(event_evidence)
    database.commit()

    assert get_evidence_for_event(database, event.id)[0].id == evidence.id
    assert event_evidence.evidence_id == evidence.id

    intelligence_run = MeetingIntelligenceRun(
        meeting_id=meeting.id,
        provider="test-provider",
        configuration_key="config-2",
        status=MeetingIntelligenceStatus.COMPLETED,
    )
    database.add(intelligence_run)
    database.flush()
    intelligence = MeetingIntelligence(
        meeting_id=meeting.id,
        generation_run_id=intelligence_run.id,
        provider="test-provider",
        configuration_key="config-2",
        status=MeetingIntelligenceStatus.COMPLETED,
        summary="summary",
        sections={"commitments": [{"event_ids": [event.id]}]},
    )
    database.add(intelligence)
    database.commit()

    assert get_evidence_for_event(database, event.id)[0].transcript_segment_id == segments[0].id


def test_invalid_transcript_and_cross_meeting_rejection(database: Session) -> None:
    meeting, segments, speaker_a, _ = _meeting_with_segments(database)

    with pytest.raises(EvidenceServiceError, match="Transcript segment not found"):
        create_evidence(
            database,
            meeting_id=meeting.id,
            evidence_type=EvidenceType.TRANSCRIPT,
            transcript_segment_id="missing-segment",
            speaker_id=speaker_a.id,
            start_time=0.0,
            end_time=3.0,
            content="bad",
        )

    other_meeting, other_segments, _, _ = _meeting_with_segments(database)
    with pytest.raises(EvidenceServiceError, match="must belong to the same meeting"):
        create_evidence(
            database,
            meeting_id=meeting.id,
            evidence_type=EvidenceType.TRANSCRIPT,
            transcript_segment_id=other_segments[0].id,
            speaker_id=speaker_a.id,
            start_time=0.0,
            end_time=3.0,
            content="bad",
        )

    with pytest.raises(EvidenceServiceError, match="Invalid timestamp"):
        create_evidence(
            database,
            meeting_id=meeting.id,
            evidence_type=EvidenceType.TRANSCRIPT,
            transcript_segment_id=segments[0].id,
            speaker_id=speaker_a.id,
            start_time=10.0,
            end_time=3.0,
            content="bad",
        )

    other_speaker = Speaker(meeting_id=meeting.id, label="SPEAKER_02")
    database.add(other_speaker)
    database.commit()
    with pytest.raises(EvidenceServiceError, match="speaker does not belong"):
        create_evidence(
            database,
            meeting_id=meeting.id,
            evidence_type=EvidenceType.TRANSCRIPT,
            transcript_segment_id=segments[0].id,
            speaker_id=other_speaker.id,
            start_time=0.0,
            end_time=3.0,
            content="bad",
        )


def test_evidence_api_endpoints(database: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    meeting, segments, speaker_a, _ = _meeting_with_segments(database)
    evidence = create_evidence(
        database,
        meeting_id=meeting.id,
        evidence_type=EvidenceType.TRANSCRIPT,
        transcript_segment_id=segments[0].id,
        speaker_id=speaker_a.id,
        start_time=0.0,
        end_time=5.0,
        content=segments[0].text,
    )

    extraction_run = EventExtractionRun(
        meeting_id=meeting.id,
        provider="test-provider",
        configuration_key="config-3",
        status=EventExtractionStatus.COMPLETED,
    )
    database.add(extraction_run)
    database.flush()
    event = Event(
        meeting_id=meeting.id,
        extraction_run_id=extraction_run.id,
        speaker_id=speaker_a.id,
        event_type="COMMITMENT",
        title="Ship API Friday",
        original_text=segments[0].text,
        start_time=segments[0].start_time,
        end_time=segments[0].end_time,
        confidence_score=0.99,
    )
    database.add(event)
    database.flush()
    database.add(
        EventEvidence(
            event_id=event.id,
            evidence_id=evidence.id,
            transcript_segment_id=segments[0].id,
            evidence_start=0.0,
            evidence_end=5.0,
            relevance=0.99,
        )
    )
    database.commit()

    app.dependency_overrides[get_db] = lambda: database
    try:
        with TestClient(app) as client:
            list_response = client.get(f"/api/meetings/{meeting.id}/evidence")
            assert list_response.status_code == 200
            assert list_response.json()["data"][0]["transcript_segment_id"] == segments[0].id

            detail = client.get(f"/api/meetings/{meeting.id}/evidence/{evidence.id}")
            assert detail.status_code == 200
            assert detail.json()["data"]["id"] == evidence.id

            event_response = client.get(f"/api/meetings/{meeting.id}/events/{event.id}/evidence")
            assert event_response.status_code == 200
            assert event_response.json()["data"][0]["evidence_id"] == evidence.id
    finally:
        app.dependency_overrides.clear()
