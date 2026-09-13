from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.entities import (
    Event,
    EventRelationship,
    EventRelationshipType,
    EventTemporalState,
    Meeting,
)


class TemporalRelationshipError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _validate_event_exists(db: Session, meeting_id: str, event_id: str, *, label: str) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise TemporalRelationshipError(f"{label.upper()}_NOT_FOUND", f"{label} event not found", 404)
    if event.meeting_id != meeting_id:
        raise TemporalRelationshipError("CROSS_MEETING_RELATIONSHIP", "Event must belong to the same meeting", 422)
    return event


def _validate_relationship_type(value: EventRelationshipType | str) -> EventRelationshipType:
    if isinstance(value, EventRelationshipType):
        return value
    try:
        return EventRelationshipType(value)
    except ValueError as exc:
        raise TemporalRelationshipError("INVALID_RELATIONSHIP_TYPE", "relationship type is not valid", 422) from exc


def _validate_confidence(confidence: float) -> float:
    if confidence < 0 or confidence > 1:
        raise TemporalRelationshipError("INVALID_CONFIDENCE", "confidence must be between 0 and 1", 422)
    return confidence


def _duplicate_relationship_exists(db: Session, source_event_id: str, target_event_id: str, relationship_type: EventRelationshipType) -> bool:
    existing = db.scalar(
        select(EventRelationship).where(
            EventRelationship.source_event_id == source_event_id,
            EventRelationship.target_event_id == target_event_id,
            EventRelationship.relationship_type == relationship_type,
        )
    )
    return existing is not None


def create_event_relationship(
    db: Session,
    meeting_id: str,
    source_event_id: str,
    target_event_id: str,
    relationship_type: EventRelationshipType | str,
    confidence: float = 0.0,
    rationale: str | None = None,
    metadata: dict | None = None,
    update_state: bool = False,
) -> EventRelationship:
    if db.get(Meeting, meeting_id) is None:
        raise TemporalRelationshipError("MEETING_NOT_FOUND", "Meeting not found", 404)
    source_event = _validate_event_exists(db, meeting_id, source_event_id, label="source")
    target_event = _validate_event_exists(db, meeting_id, target_event_id, label="target")
    if source_event.id == target_event.id:
        raise TemporalRelationshipError("SELF_REFERENCE", "source and target event must be the same event", 422)

    normalized_type = _validate_relationship_type(relationship_type)
    normalized_confidence = _validate_confidence(confidence)

    if _duplicate_relationship_exists(db, source_event_id, target_event_id, normalized_type):
        raise TemporalRelationshipError("DUPLICATE_RELATIONSHIP", "relationship already exists", 409)

    relationship = EventRelationship(
        meeting_id=meeting_id,
        source_event_id=source_event_id,
        target_event_id=target_event_id,
        relationship_type=normalized_type,
        confidence=normalized_confidence,
        rationale=rationale,
        metadata_json=metadata,
    )
    db.add(relationship)
    db.flush()

    if update_state:
        update_event_temporal_state(db, target_event_id, normalized_type)

    db.commit()
    return relationship


def update_event_temporal_state(db: Session, event_id: str, relationship_type: EventRelationshipType | str) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise TemporalRelationshipError("EVENT_NOT_FOUND", "Event not found", 404)

    normalized_type = _validate_relationship_type(relationship_type)
    if normalized_type == EventRelationshipType.SUPERSEDES:
        event.temporal_state = EventTemporalState.SUPERSEDED
    elif normalized_type == EventRelationshipType.CANCELS:
        event.temporal_state = EventTemporalState.CANCELLED
    elif normalized_type == EventRelationshipType.COMPLETES:
        event.temporal_state = EventTemporalState.COMPLETED
    elif normalized_type == EventRelationshipType.REOPENS:
        event.temporal_state = EventTemporalState.REOPENED
    else:
        event.temporal_state = EventTemporalState.ACTIVE

    db.flush()
    return event


def get_event_relationships(db: Session, meeting_id: str, event_id: str) -> list[EventRelationship]:
    event = db.get(Event, event_id)
    if event is None:
        return []
    if event.meeting_id != meeting_id:
        raise TemporalRelationshipError("CROSS_MEETING_RELATIONSHIP", "Event does not belong to the requested meeting", 422)
    return list(
        db.scalars(
            select(EventRelationship)
            .where(EventRelationship.meeting_id == meeting_id)
            .where(
                (EventRelationship.source_event_id == event_id) | (EventRelationship.target_event_id == event_id)
            )
            .order_by(EventRelationship.created_at, EventRelationship.id)
        ).all()
    )


def get_outgoing_event_relationships(db: Session, meeting_id: str, event_id: str) -> list[EventRelationship]:
    event = db.get(Event, event_id)
    if event is None:
        return []
    if event.meeting_id != meeting_id:
        raise TemporalRelationshipError("CROSS_MEETING_RELATIONSHIP", "Event does not belong to the requested meeting", 422)
    return list(
        db.scalars(
            select(EventRelationship)
            .where(EventRelationship.meeting_id == meeting_id)
            .where(EventRelationship.source_event_id == event_id)
            .order_by(EventRelationship.created_at, EventRelationship.id)
        ).all()
    )


def get_incoming_event_relationships(db: Session, meeting_id: str, event_id: str) -> list[EventRelationship]:
    event = db.get(Event, event_id)
    if event is None:
        return []
    if event.meeting_id != meeting_id:
        raise TemporalRelationshipError("CROSS_MEETING_RELATIONSHIP", "Event does not belong to the requested meeting", 422)
    return list(
        db.scalars(
            select(EventRelationship)
            .where(EventRelationship.meeting_id == meeting_id)
            .where(EventRelationship.target_event_id == event_id)
            .order_by(EventRelationship.created_at, EventRelationship.id)
        ).all()
    )
