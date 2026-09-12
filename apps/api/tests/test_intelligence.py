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
    Event,
    EventExtractionRun,
    EventExtractionStatus,
    EventType,
    Meeting,
    MeetingIntelligence,
    MeetingIntelligenceRun,
    MeetingIntelligenceStatus,
    MeetingStatus,
    SourceType,
)
from app.services.intelligence import (
    IntelligenceError,
    IntelligenceEventInput,
    IntelligenceItem,
    MeetingIntelligenceOutput,
    MeetingIntelligenceProvider,
    generate_meeting_intelligence,
)
from app.services.jobs import JobStatus


class DeterministicFakeIntelligenceProvider(MeetingIntelligenceProvider):
    name = "test-fake-intelligence"
    model_name = "deterministic"
    temperature = 0.0

    def __init__(self, output: MeetingIntelligenceOutput, failure: Exception | None = None) -> None:
        self.output = output
        self.failure = failure
        self.calls = 0

    def synthesize(
        self,
        events: list[IntelligenceEventInput],
        meeting_context: str | None = None,
    ) -> MeetingIntelligenceOutput:
        self.calls += 1
        if self.failure:
            raise self.failure
        return self.output


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'intelligence.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def create_event_fixture(session: Session) -> tuple[Meeting, list[Event]]:
    meeting = Meeting(title="Deployment discussion", source_type=SourceType.AUDIO, status=MeetingStatus.UPLOADED)
    session.add(meeting)
    session.flush()
    extraction_run = EventExtractionRun(
        meeting_id=meeting.id,
        provider="test-fake-events",
        provider_model="deterministic",
        configuration_key="events-config",
        status=EventExtractionStatus.COMPLETED,
    )
    session.add(extraction_run)
    session.flush()
    events = [
        Event(
            meeting_id=meeting.id,
            extraction_run_id=extraction_run.id,
            event_type=EventType.COMMITMENT,
            title="Deploy API",
            subject="API deployment",
            value="Friday",
            original_text="I'll deploy the new API this Friday.",
            start_time=6,
            end_time=11,
            confidence_score=0.94,
        ),
        Event(
            meeting_id=meeting.id,
            extraction_run_id=extraction_run.id,
            event_type=EventType.RISK,
            title="Security review incomplete",
            subject="Security review",
            value="Incomplete",
            original_text="The security review isn't complete.",
            start_time=12,
            end_time=17,
            confidence_score=0.88,
        ),
        Event(
            meeting_id=meeting.id,
            extraction_run_id=extraction_run.id,
            event_type=EventType.PROPOSAL,
            title="Deploy next Monday",
            subject="API deployment",
            value="Next Monday",
            original_text="Could we instead deploy next Monday?",
            start_time=18,
            end_time=23,
            confidence_score=0.81,
        ),
    ]
    session.add_all(events)
    session.commit()
    return meeting, events


def intelligence_output(events: list[Event]) -> MeetingIntelligenceOutput:
    return MeetingIntelligenceOutput(
        summary="The meeting covered deployment timing and an incomplete security review.",
        commitments=[IntelligenceItem(title="Deploy API", summary="Deploy API Friday.", event_ids=[events[0].id], confidence=0.94)],
        risks=[IntelligenceItem(title="Security review", summary="Security review is incomplete.", event_ids=[events[1].id], confidence=0.88)],
        proposals=[IntelligenceItem(title="Deploy next Monday", summary="Monday was proposed as an alternative.", event_ids=[events[2].id], confidence=0.81)],
        highlights=[IntelligenceItem(title="Deployment timing", summary="Deployment timing was discussed.", event_ids=[events[0].id, events[2].id])],
        open_items=[IntelligenceItem(title="Deployment timing", summary="Deployment timing requires clarification.", event_ids=[events[0].id, events[2].id])],
    )


def test_intelligence_persistence_sections_and_temporal_boundary(database) -> None:
    meeting, events = create_event_fixture(database)
    provider = DeterministicFakeIntelligenceProvider(intelligence_output(events))

    state = generate_meeting_intelligence(database, meeting.id, provider)

    assert state.intelligence is not None
    assert state.intelligence.status == MeetingIntelligenceStatus.COMPLETED
    assert state.intelligence.summary.startswith("The meeting")
    assert state.intelligence.sections["commitments"][0]["event_ids"] == [events[0].id]
    assert state.intelligence.sections["proposals"][0]["event_ids"] == [events[2].id]
    assert "supersedes_event_id" not in state.intelligence.sections["proposals"][0]
    assert database.scalar(select(func.count(Event.id))) == 3


def test_invalid_cross_meeting_reference_is_rejected(database) -> None:
    meeting_a, events_a = create_event_fixture(database)
    meeting_b, events_b = create_event_fixture(database)
    output = MeetingIntelligenceOutput(
        summary="Invalid cross-meeting output",
        commitments=[IntelligenceItem(title="Bad", summary="Bad", event_ids=[events_b[0].id])],
    )
    with pytest.raises(IntelligenceError, match="Invalid event reference"):
        generate_meeting_intelligence(database, meeting_a.id, DeterministicFakeIntelligenceProvider(output))
    assert database.scalar(select(func.count(MeetingIntelligence.id)).where(MeetingIntelligence.meeting_id == meeting_a.id)) == 0


def test_empty_events_and_prerequisite(database) -> None:
    meeting = Meeting(title="No events", source_type=SourceType.AUDIO, status=MeetingStatus.UPLOADED)
    database.add(meeting)
    database.commit()
    with pytest.raises(IntelligenceError, match="Event extraction must be completed"):
        generate_meeting_intelligence(database, meeting.id, DeterministicFakeIntelligenceProvider(MeetingIntelligenceOutput(summary="none")))

    extraction_run = EventExtractionRun(
        meeting_id=meeting.id,
        provider="test-fake-events",
        configuration_key="empty-events",
        status=EventExtractionStatus.COMPLETED,
    )
    database.add(extraction_run)
    database.commit()
    output = MeetingIntelligenceOutput(summary="No structured events were detected in this meeting.")
    state = generate_meeting_intelligence(database, meeting.id, DeterministicFakeIntelligenceProvider(output))
    assert state.intelligence.sections["decisions"] == []


def test_idempotency_and_retry(database) -> None:
    meeting, events = create_event_fixture(database)
    provider = DeterministicFakeIntelligenceProvider(intelligence_output(events))
    first = generate_meeting_intelligence(database, meeting.id, provider)
    second = generate_meeting_intelligence(database, meeting.id, provider)
    assert second.reused is True
    assert provider.calls == 1
    assert database.scalar(select(func.count(MeetingIntelligenceRun.id)).where(MeetingIntelligenceRun.meeting_id == meeting.id)) == 1

    retry_meeting, retry_events = create_event_fixture(database)
    failing = DeterministicFakeIntelligenceProvider(MeetingIntelligenceOutput(summary="unused"), IntelligenceError("provider failed"))
    with pytest.raises(IntelligenceError, match="provider failed"):
        generate_meeting_intelligence(database, retry_meeting.id, failing)
    retry = generate_meeting_intelligence(database, retry_meeting.id, DeterministicFakeIntelligenceProvider(intelligence_output(retry_events)))
    assert retry.run.status == MeetingIntelligenceStatus.COMPLETED


def test_intelligence_api_and_worker(database, monkeypatch: pytest.MonkeyPatch) -> None:
    meeting, events = create_event_fixture(database)
    provider = DeterministicFakeIntelligenceProvider(intelligence_output(events))
    generate_meeting_intelligence(database, meeting.id, provider)

    import app.api.intelligence as intelligence_api

    monkeypatch.setattr(intelligence_api, "create_meeting_intelligence_provider", lambda: provider)
    monkeypatch.setattr(
        intelligence_api,
        "enqueue_meeting_intelligence_job",
        lambda meeting_id: JobStatus("intelligence-job", meeting_id, "QUEUED"),
    )
    app.dependency_overrides[get_db] = lambda: database
    try:
        with TestClient(app) as client:
            latest = client.get(f"/api/meetings/{meeting.id}/intelligence")
            assert latest.status_code == 200
            intelligence_id = latest.json()["data"]["id"]
            detail = client.get(f"/api/meetings/{meeting.id}/intelligence/{intelligence_id}")
            assert detail.status_code == 200
            status = client.get(f"/api/meetings/{meeting.id}/intelligence/status")
            assert status.status_code == 200
            assert status.json()["data"]["event_count"] == 3
            queued = client.post(f"/api/meetings/{meeting.id}/intelligence/generate")
            assert queued.status_code == 200
            assert queued.json()["data"]["status"] == "COMPLETED"
    finally:
        app.dependency_overrides.clear()

    import app.worker as worker

    class DummySession:
        def close(self) -> None:
            pass

    statuses: list[str] = []
    monkeypatch.setattr(worker, "SessionLocal", lambda: DummySession())
    monkeypatch.setattr(worker, "update_job", lambda job_id, meeting_id, status, error=None: statuses.append(status.value if hasattr(status, "value") else status))
    monkeypatch.setattr(worker, "create_meeting_intelligence_provider", lambda: provider)
    monkeypatch.setattr(worker, "generate_meeting_intelligence", lambda db, meeting_id, provider: None)
    worker.process_transcription_job({"kind": "meeting_intelligence", "job_id": "job", "meeting_id": meeting.id})
    assert statuses == ["PROCESSING", "COMPLETED"]
