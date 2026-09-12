from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.entities import (
    Event,
    EventEvidence,
    EventExtractionRun,
    EventExtractionStatus,
    EventType,
    Meeting,
    Speaker,
    TranscriptSegment,
)
from app.services.event_extraction import (
    EventExtractionError,
    EventExtractionProvider,
    ExtractedEvent,
    TranscriptExtractionInput,
)

logger = logging.getLogger(__name__)


class EventServiceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class EventState:
    run: EventExtractionRun
    events: list[Event]
    reused: bool = False


def extraction_configuration_key(provider: EventExtractionProvider) -> str:
    values = {
        "provider": getattr(provider, "name", provider.__class__.__name__),
        "model": getattr(provider, "model_name", None),
        "temperature": getattr(provider, "temperature", None),
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()


def _find_run(db: Session, meeting_id: str, configuration_key: str) -> EventExtractionRun | None:
    return db.scalar(
        select(EventExtractionRun).where(
            EventExtractionRun.meeting_id == meeting_id,
            EventExtractionRun.configuration_key == configuration_key,
        )
    )


def extract_meeting_events(
    db: Session,
    meeting_id: str,
    provider: EventExtractionProvider,
) -> EventState:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise EventServiceError("MEETING_NOT_FOUND", "Meeting not found", 404)

    configuration_key = extraction_configuration_key(provider)
    run = _find_run(db, meeting_id, configuration_key)
    if run is not None and run.status == EventExtractionStatus.COMPLETED:
        events = list(
            db.scalars(
                select(Event).where(Event.extraction_run_id == run.id).order_by(Event.start_time, Event.id)
            ).all()
        )
        return EventState(run, events, reused=True)

    if run is None:
        run = EventExtractionRun(
            meeting_id=meeting_id,
            provider=getattr(provider, "name", provider.__class__.__name__),
            provider_model=getattr(provider, "model_name", None),
            configuration_key=configuration_key,
            status=EventExtractionStatus.QUEUED,
        )
        db.add(run)
        db.flush()
    else:
        run.status = EventExtractionStatus.QUEUED
        run.error = None
    meeting.processing_status = EventExtractionStatus.QUEUED.value
    db.commit()

    run.status = EventExtractionStatus.PROCESSING
    run.started_at = datetime.now(timezone.utc)
    meeting.processing_status = EventExtractionStatus.PROCESSING.value
    db.commit()
    try:
        transcript_segments = list(
            db.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.meeting_id == meeting_id)
                .order_by(TranscriptSegment.sequence, TranscriptSegment.start_time)
            ).all()
        )
        speaker_ids = {
            speaker.id: speaker.label
            for speaker in db.scalars(select(Speaker).where(Speaker.meeting_id == meeting_id)).all()
        }
        provider_segments = [
            TranscriptExtractionInput(
                id=segment.id,
                speaker_id=speaker_ids.get(segment.speaker_id),
                start_time=segment.start_time,
                end_time=segment.end_time,
                text=segment.text,
            )
            for segment in transcript_segments
        ]
        extracted = provider.extract_events(provider_segments, meeting.title)
        events = _persist_events(db, meeting_id, run, transcript_segments, extracted)
        run.status = EventExtractionStatus.COMPLETED
        run.completed_at = datetime.now(timezone.utc)
        run.error = None
        meeting.processing_status = EventExtractionStatus.COMPLETED.value
        db.commit()
        logger.info(
            "event extraction completed meeting_id=%s provider=%s model=%s status=%s event_count=%s",
            meeting_id,
            run.provider,
            run.provider_model,
            run.status.value,
            len(events),
        )
        return EventState(run, events)
    except (EventExtractionError, EventServiceError) as exc:
        _mark_failed(db, run, meeting, str(exc))
        logger.error(
            "event extraction failed meeting_id=%s provider=%s model=%s status=%s error=%s",
            meeting_id,
            run.provider,
            run.provider_model,
            EventExtractionStatus.FAILED.value,
            str(exc),
        )
        if isinstance(exc, EventServiceError):
            raise
        raise EventServiceError("EVENT_EXTRACTION_FAILED", str(exc)) from exc
    except Exception as exc:
        _mark_failed(db, run, meeting, "Unexpected event extraction failure")
        logger.exception("event extraction failed meeting_id=%s status=%s", meeting_id, EventExtractionStatus.FAILED.value)
        raise EventServiceError("EVENT_EXTRACTION_FAILED", "Unexpected event extraction failure") from exc


def _persist_events(
    db: Session,
    meeting_id: str,
    run: EventExtractionRun,
    transcript_segments: list[TranscriptSegment],
    extracted: list[ExtractedEvent],
) -> list[Event]:
    segment_by_id = {segment.id: segment for segment in transcript_segments}
    db.execute(delete(Event).where(Event.extraction_run_id == run.id))
    events: list[Event] = []
    for candidate in extracted:
        evidence_segments = [segment_by_id.get(segment_id) for segment_id in candidate.transcript_segment_ids]
        if any(segment is None for segment in evidence_segments):
            raise EventServiceError(
                "INVALID_EVENT_EVIDENCE",
                "Event references a transcript segment outside this meeting",
                422,
            )
        valid_segments = [segment for segment in evidence_segments if segment is not None]
        if not valid_segments:
            raise EventServiceError("INVALID_EVENT_EVIDENCE", "Event must reference transcript evidence", 422)
        speaker_ids = {segment.speaker_id for segment in valid_segments if segment.speaker_id}
        event = Event(
            meeting_id=meeting_id,
            extraction_run_id=run.id,
            event_type=candidate.event_type,
            title=candidate.title,
            subject=candidate.subject,
            value=candidate.value,
            object_value=candidate.value,
            speaker_id=next(iter(speaker_ids)) if len(speaker_ids) == 1 else None,
            start_time=min(segment.start_time for segment in valid_segments),
            end_time=max(segment.end_time for segment in valid_segments),
            original_text="\n".join(segment.text for segment in valid_segments),
            confidence_score=candidate.confidence,
            schema_version=1,
        )
        db.add(event)
        db.flush()
        for segment in valid_segments:
            db.add(
                EventEvidence(
                    event_id=event.id,
                    transcript_segment_id=segment.id,
                    evidence_start=segment.start_time,
                    evidence_end=segment.end_time,
                    relevance=candidate.confidence,
                )
            )
        events.append(event)
    db.flush()
    return events


def _mark_failed(db: Session, run: EventExtractionRun, meeting: Meeting, error: str) -> None:
    db.rollback()
    run.status = EventExtractionStatus.FAILED
    run.error = error
    meeting.processing_status = EventExtractionStatus.FAILED.value
    db.commit()


def list_events(
    db: Session,
    meeting_id: str,
    event_type: EventType | None = None,
    speaker_id: str | None = None,
) -> list[Event]:
    statement = select(Event).where(Event.meeting_id == meeting_id)
    if event_type is not None:
        statement = statement.where(Event.event_type == event_type)
    if speaker_id is not None:
        statement = statement.where(Event.speaker_id == speaker_id)
    return list(db.scalars(statement.order_by(Event.start_time, Event.id)).all())


def get_event(db: Session, meeting_id: str, event_id: str) -> Event | None:
    return db.scalar(select(Event).where(Event.meeting_id == meeting_id, Event.id == event_id))


def get_event_evidence(db: Session, event_id: str) -> list[tuple[EventEvidence, TranscriptSegment]]:
    return list(
        db.execute(
            select(EventEvidence, TranscriptSegment)
            .join(TranscriptSegment, TranscriptSegment.id == EventEvidence.transcript_segment_id)
            .where(EventEvidence.event_id == event_id)
            .order_by(EventEvidence.evidence_start)
        ).all()
    )


def get_extraction_status(db: Session, meeting_id: str) -> EventExtractionRun | None:
    return db.scalar(
        select(EventExtractionRun)
        .where(EventExtractionRun.meeting_id == meeting_id)
        .order_by(EventExtractionRun.created_at.desc())
    )
