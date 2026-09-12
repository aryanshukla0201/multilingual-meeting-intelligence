from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import (
    Decision,
    DecisionStatus,
    Event,
    EventType,
    Meeting,
    MeetingStatus,
    Organization,
    Project,
    SourceType,
    Transcript,
    TranscriptSegment,
)
from app.schemas.contracts import EventRead, MeetingCreate, MeetingRead


engine = create_engine("sqlite:///:memory:")


def setup_function() -> None:
    Base.metadata.create_all(engine)


def teardown_function() -> None:
    Base.metadata.drop_all(engine)


def test_phase1_schema_contains_required_tables_and_indexes() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert {"meetings", "transcripts", "transcript_segments", "events", "evidence"}.issubset(tables)
    indexes = {index["name"] for index in inspector.get_indexes("events")}
    assert "ix_events_meeting_time_type" in indexes


def test_meeting_transcript_event_decision_persist_with_foreign_keys() -> None:
    with Session(engine) as session:
        organization = Organization(name="Acme")
        session.add(organization)
        session.flush()

        project = Project(organization_id=organization.id, name="Platform")
        meeting = Meeting(
            organization_id=organization.id,
            project_id=project.id,
            title="Deployment planning",
            source_type=SourceType.AUDIO,
            status=MeetingStatus.UPLOADED,
        )
        session.add_all([project, meeting])
        session.flush()

        transcript = Transcript(meeting_id=meeting.id, detected_language="en")
        session.add(transcript)
        session.flush()
        segment = TranscriptSegment(
            transcript_id=transcript.id,
            meeting_id=meeting.id,
            segment_index=0,
            start_time=10.0,
            end_time=14.0,
            text="We will deploy Monday.",
        )
        event = Event(
            meeting_id=meeting.id,
            event_type=EventType.COMMITMENT,
            start_time=10.0,
            end_time=14.0,
            original_text=segment.text,
            subject="deployment",
            predicate="scheduled_for",
            object_value="Monday",
            confidence_score=0.95,
        )
        session.add_all([segment, event])
        session.flush()
        decision = Decision(
            meeting_id=meeting.id,
            event_id=event.id,
            statement="Deploy Monday",
            timestamp=10.0,
            status=DecisionStatus.ACCEPTED,
        )
        session.add(decision)
        session.commit()

        saved_meeting = session.get(Meeting, meeting.id)
        saved_event = session.get(Event, event.id)

    assert saved_meeting is not None
    assert saved_event is not None
    assert saved_event.object_value == "Monday"
    assert saved_event.event_type == EventType.COMMITMENT


def test_pydantic_contracts_validate_database_models() -> None:
    request = MeetingCreate(title="Planning", source_type=SourceType.VIDEO)
    assert request.title == "Planning"

    meeting = Meeting(id="meeting-id", title="Planning", source_type=SourceType.VIDEO)
    meeting.status = MeetingStatus.COMPLETED
    meeting.processing_progress = 100
    meeting.created_at = meeting.updated_at = __import__("datetime").datetime.now()
    response = MeetingRead.model_validate(meeting)
    assert response.status is MeetingStatus.COMPLETED

    event = Event(
        id="event-id",
        meeting_id="meeting-id",
        event_type=EventType.DECISION,
        start_time=2,
        end_time=3,
        original_text="Use PostgreSQL",
        schema_version=1,
    )
    event.created_at = event.updated_at = __import__("datetime").datetime.now()
    event_response = EventRead.model_validate(event)
    assert event_response.event_type is EventType.DECISION
