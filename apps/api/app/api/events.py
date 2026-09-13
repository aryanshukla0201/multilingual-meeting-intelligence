from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import (
    Event,
    EventExtractionRun,
    EventExtractionStatus,
    EventType,
    Meeting,
    Speaker,
)
from app.schemas.contracts import (
    EventEvidenceRead,
    EventExtractionJobResponse,
    EventExtractionStatusRead,
    MeetingEventRead,
)
from app.services.event_extraction import EventExtractionError, create_event_extraction_provider
from app.services.event_service import (
    EventServiceError,
    extraction_configuration_key,
    get_event,
    get_event_evidence,
    get_extraction_status,
    list_events,
)
from app.services.jobs import JobQueueError, enqueue_event_extraction_job

router = APIRouter(prefix="/api", tags=["events"])


def _success(data: object, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": True, "data": data, "error": None, "meta": {}},
    )


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "data": None, "error": {"code": code, "message": message}, "meta": {}},
    )


def _event_data(db: Session, event: Event) -> dict:
    speaker = db.get(Speaker, event.speaker_id) if event.speaker_id else None
    evidence = []
    for event_evidence, segment in get_event_evidence(db, event.id):
        evidence_speaker = db.get(Speaker, segment.speaker_id) if segment.speaker_id else None
        evidence.append(
            EventEvidenceRead(
                id=event_evidence.id,
                evidence_id=event_evidence.evidence_id,
                transcript_segment_id=segment.id,
                start_time=event_evidence.evidence_start,
                end_time=event_evidence.evidence_end,
                text=segment.text,
                speaker_id=segment.speaker_id,
                speaker_label=evidence_speaker.label if evidence_speaker else None,
            )
        )
    return MeetingEventRead(
        id=event.id,
        meeting_id=event.meeting_id,
        extraction_run_id=event.extraction_run_id,
        event_type=event.event_type,
        title=event.title,
        subject=event.subject,
        value=event.value,
        speaker_id=event.speaker_id,
        speaker_label=speaker.label if speaker else None,
        speaker_display_name=speaker.display_name if speaker else None,
        start_time=event.start_time,
        end_time=event.end_time,
        confidence=event.confidence_score,
        evidence=evidence,
    ).model_dump(mode="json")


@router.post("/meetings/{meeting_id}/events/extract", status_code=202, summary="Queue event extraction")
def queue_event_extraction(meeting_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    if db.get(Meeting, meeting_id) is None:
        return _error("MEETING_NOT_FOUND", "Meeting not found", 404)
    try:
        provider = create_event_extraction_provider()
    except (EventExtractionError, JobQueueError) as exc:
        return _error("EVENT_EXTRACTION_UNAVAILABLE", str(exc), 503)
    configuration_key = extraction_configuration_key(provider)
    run = db.scalar(
        select(EventExtractionRun).where(
            EventExtractionRun.meeting_id == meeting_id,
            EventExtractionRun.configuration_key == configuration_key,
        )
    )
    if run is not None and run.status == EventExtractionStatus.COMPLETED:
        response = EventExtractionJobResponse(
            job_id="",
            meeting_id=meeting_id,
            status=EventExtractionStatus.COMPLETED,
        )
        return _success(response.model_dump(mode="json"), 200)
    try:
        job = enqueue_event_extraction_job(meeting_id)
    except JobQueueError as exc:
        return _error("EVENT_EXTRACTION_UNAVAILABLE", str(exc), 503)
    if run is None:
        db.add(
            EventExtractionRun(
                meeting_id=meeting_id,
                provider=provider.name,
                provider_model=getattr(provider, "model_name", None),
                configuration_key=configuration_key,
                status=EventExtractionStatus.QUEUED,
            )
        )
    else:
        run.status = EventExtractionStatus.QUEUED
        run.error = None
    db.commit()
    response = EventExtractionJobResponse(
        job_id=job.job_id,
        meeting_id=meeting_id,
        status=EventExtractionStatus.QUEUED,
    )
    return _success(response.model_dump(mode="json"), 202)


@router.get("/meetings/{meeting_id}/events", summary="List extracted meeting events")
def read_events(
    meeting_id: str,
    event_type: EventType | None = Query(None),
    speaker_id: str | None = Query(None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    return _success([_event_data(db, event) for event in list_events(db, meeting_id, event_type, speaker_id)])


@router.get("/meetings/{meeting_id}/events/{event_id}", summary="Get event detail and evidence")
def read_event(meeting_id: str, event_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    event = get_event(db, meeting_id, event_id)
    if event is None:
        return _error("EVENT_NOT_FOUND", "Event not found", 404)
    return _success(_event_data(db, event))


@router.get("/meetings/{meeting_id}/events/extraction/status", summary="Get event extraction status")
def read_event_extraction_status(meeting_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    run = get_extraction_status(db, meeting_id)
    if run is None:
        response = EventExtractionStatusRead(
            status=EventExtractionStatus.NOT_STARTED,
            provider=None,
            model=None,
            started_at=None,
            completed_at=None,
            error=None,
            event_count=0,
        )
    else:
        event_count = db.scalar(select(func.count(Event.id)).where(Event.extraction_run_id == run.id)) or 0
        response = EventExtractionStatusRead(
            status=run.status,
            provider=run.provider,
            model=run.provider_model,
            started_at=run.started_at,
            completed_at=run.completed_at,
            error=run.error,
            event_count=int(event_count),
        )
    return _success(response.model_dump(mode="json"))
