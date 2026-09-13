from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import (
    Event,
    EventRelationship,
    EventRelationshipType,
    EventTemporalState,
    EventType,
    Meeting,
    MeetingStatus,
    SourceType,
)
from app.services.temporal_service import (
    TemporalRelationshipError,
    create_event_relationship,
    get_event_relationships,
    get_incoming_event_relationships,
    get_outgoing_event_relationships,
    update_event_temporal_state,
)


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'temporal.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def _make_meeting(session: Session, title: str) -> Meeting:
    meeting = Meeting(title=title, source_type=SourceType.AUDIO, status=MeetingStatus.UPLOADED)
    session.add(meeting)
    session.flush()
    return meeting


def _make_event(session: Session, meeting_id: str, title: str, start_time: float, end_time: float) -> Event:
    event = Event(
        meeting_id=meeting_id,
        event_type=EventType.COMMITMENT,
        title=title,
        original_text=title,
        start_time=start_time,
        end_time=end_time,
        confidence_score=0.9,
        temporal_state=EventTemporalState.ACTIVE,
    )
    session.add(event)
    session.flush()
    return event


def test_create_and_retrieve_temporal_relationship(database: Session) -> None:
    meeting = _make_meeting(database, "Temporal test")
    first = _make_event(database, meeting.id, "Deploy Friday", 0.0, 10.0)
    second = _make_event(database, meeting.id, "Deploy Monday", 20.0, 30.0)

    rel = create_event_relationship(
        database,
        meeting_id=meeting.id,
        source_event_id=first.id,
        target_event_id=second.id,
        relationship_type=EventRelationshipType.SUPERSEDES,
        confidence=0.92,
        rationale="Later commitment supersedes earlier plan.",
    )

    assert rel.source_event_id == first.id
    assert rel.target_event_id == second.id
    assert rel.relationship_type == EventRelationshipType.SUPERSEDES
    assert rel.confidence == 0.92
    assert get_event_relationships(database, meeting.id, first.id)[0].id == rel.id
    assert get_outgoing_event_relationships(database, meeting.id, first.id)[0].id == rel.id
    assert get_incoming_event_relationships(database, meeting.id, second.id)[0].id == rel.id


def test_relationship_state_updates_when_explicitly_requested(database: Session) -> None:
    meeting = _make_meeting(database, "Temporal state")
    first = _make_event(database, meeting.id, "Deploy Friday", 0.0, 10.0)
    second = _make_event(database, meeting.id, "Deploy Monday", 20.0, 30.0)

    rel = create_event_relationship(
        database,
        meeting_id=meeting.id,
        source_event_id=first.id,
        target_event_id=second.id,
        relationship_type=EventRelationshipType.SUPERSEDES,
        confidence=0.91,
        rationale="Supersedes earlier plan",
        update_state=True,
    )

    assert rel.target_event_id == second.id
    database.refresh(first)
    database.refresh(second)
    assert first.temporal_state == EventTemporalState.ACTIVE
    assert second.temporal_state == EventTemporalState.SUPERSEDED


def test_invalid_source_target_and_meeting_rejection(database: Session) -> None:
    meeting = _make_meeting(database, "Meeting A")
    other_meeting = _make_meeting(database, "Meeting B")
    source = _make_event(database, meeting.id, "Source event", 0.0, 10.0)
    target = _make_event(database, meeting.id, "Target event", 20.0, 30.0)
    foreign_target = _make_event(database, other_meeting.id, "Other meeting event", 0.0, 10.0)

    with pytest.raises(TemporalRelationshipError, match="source event"):
        create_event_relationship(database, meeting.id, "missing-source", target.id, EventRelationshipType.RELATES_TO)

    with pytest.raises(TemporalRelationshipError, match="target event"):
        create_event_relationship(database, meeting.id, source.id, "missing-target", EventRelationshipType.RELATES_TO)

    with pytest.raises(TemporalRelationshipError, match="same meeting"):
        create_event_relationship(database, meeting.id, source.id, foreign_target.id, EventRelationshipType.RELATES_TO)

    with pytest.raises(TemporalRelationshipError, match="same event"):
        create_event_relationship(database, meeting.id, source.id, source.id, EventRelationshipType.RELATES_TO)


def test_invalid_type_and_confidence_and_duplicate_rejection(database: Session) -> None:
    meeting = _make_meeting(database, "Meeting")
    source = _make_event(database, meeting.id, "Source", 0.0, 10.0)
    target = _make_event(database, meeting.id, "Target", 20.0, 30.0)

    with pytest.raises(TemporalRelationshipError, match="relationship type"):
        create_event_relationship(database, meeting.id, source.id, target.id, "INVALID_TYPE")

    with pytest.raises(TemporalRelationshipError, match="between 0 and 1"):
        create_event_relationship(database, meeting.id, source.id, target.id, EventRelationshipType.RELATES_TO, confidence=1.5)

    rel = create_event_relationship(database, meeting.id, source.id, target.id, EventRelationshipType.RELATES_TO, confidence=0.82)
    with pytest.raises(TemporalRelationshipError, match="already exists"):
        create_event_relationship(database, meeting.id, source.id, target.id, EventRelationshipType.RELATES_TO, confidence=0.9)

    assert rel.confidence == 0.82


def test_event_relationship_api_endpoints(database: Session) -> None:
    meeting = _make_meeting(database, "Api meeting")
    source = _make_event(database, meeting.id, "Earlier event", 0.0, 5.0)
    target = _make_event(database, meeting.id, "Later event", 10.0, 20.0)
    create_event_relationship(
        database,
        meeting_id=meeting.id,
        source_event_id=source.id,
        target_event_id=target.id,
        relationship_type=EventRelationshipType.CANCELS,
        confidence=0.88,
        rationale="Later event is canceling earlier intent.",
    )

    app.dependency_overrides[get_db] = lambda: database
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/meetings/{meeting.id}/events/{source.id}/relationships")
            assert response.status_code == 200
            payload = response.json()["data"]
            assert len(payload) == 1
            assert payload[0]["relationship_type"] == EventRelationshipType.CANCELS.value

            incoming = client.get(f"/api/meetings/{meeting.id}/events/{target.id}/relationships/incoming")
            assert incoming.status_code == 200
            assert incoming.json()["data"][0]["source_event_id"] == source.id

            outgoing = client.get(f"/api/meetings/{meeting.id}/events/{source.id}/relationships/outgoing")
            assert outgoing.status_code == 200
            assert outgoing.json()["data"][0]["target_event_id"] == target.id
    finally:
        app.dependency_overrides.clear()
