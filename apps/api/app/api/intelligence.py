from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import (
    EventExtractionRun,
    EventExtractionStatus,
    Meeting,
    MeetingIntelligenceRun,
    MeetingIntelligenceStatus,
)
from app.schemas.contracts import (
    IntelligenceGenerateResponse,
    MeetingIntelligenceRead,
    MeetingIntelligenceStatusRead,
)
from app.services.intelligence import (
    IntelligenceError,
    create_meeting_intelligence_provider,
    get_intelligence,
    get_intelligence_status,
    intelligence_configuration_key,
)
from app.services.jobs import JobQueueError, enqueue_meeting_intelligence_job

router = APIRouter(prefix="/api", tags=["intelligence"])


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


def _intelligence_data(intelligence) -> dict:
    return MeetingIntelligenceRead(
        id=intelligence.id,
        meeting_id=intelligence.meeting_id,
        generation_run_id=intelligence.generation_run_id,
        provider=intelligence.provider,
        model=intelligence.provider_model,
        configuration_key=intelligence.configuration_key,
        status=intelligence.status,
        summary=intelligence.summary,
        sections=intelligence.sections,
        generated_at=intelligence.generated_at,
        created_at=intelligence.created_at,
    ).model_dump(mode="json")


@router.post("/meetings/{meeting_id}/intelligence/generate", status_code=202, summary="Queue meeting intelligence generation")
def queue_intelligence(meeting_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    if db.get(Meeting, meeting_id) is None:
        return _error("MEETING_NOT_FOUND", "Meeting not found", 404)
    extraction_run = db.scalar(
        select(EventExtractionRun)
        .where(
            EventExtractionRun.meeting_id == meeting_id,
            EventExtractionRun.status == EventExtractionStatus.COMPLETED,
        )
        .order_by(EventExtractionRun.completed_at.desc())
    )
    if extraction_run is None:
        return _error(
            "EVENT_EXTRACTION_REQUIRED",
            "Event extraction must be completed before meeting intelligence can be generated",
            409,
        )
    try:
        provider = create_meeting_intelligence_provider()
    except IntelligenceError as exc:
        return _error("INTELLIGENCE_UNAVAILABLE", str(exc), 503)
    configuration_key = intelligence_configuration_key(provider)
    run = db.scalar(
        select(MeetingIntelligenceRun).where(
            MeetingIntelligenceRun.meeting_id == meeting_id,
            MeetingIntelligenceRun.configuration_key == configuration_key,
        )
    )
    if run is not None and run.status == MeetingIntelligenceStatus.COMPLETED:
        response = IntelligenceGenerateResponse(job_id="", meeting_id=meeting_id, status=run.status)
        return _success(response.model_dump(mode="json"))
    if run is not None and run.status in {MeetingIntelligenceStatus.QUEUED, MeetingIntelligenceStatus.PROCESSING}:
        response = IntelligenceGenerateResponse(job_id="", meeting_id=meeting_id, status=run.status)
        return _success(response.model_dump(mode="json"))
    try:
        job = enqueue_meeting_intelligence_job(meeting_id)
    except JobQueueError as exc:
        return _error("INTELLIGENCE_QUEUE_UNAVAILABLE", str(exc), 503)
    if run is None:
        db.add(
            MeetingIntelligenceRun(
                meeting_id=meeting_id,
                provider=provider.name,
                provider_model=getattr(provider, "model_name", None),
                configuration_key=configuration_key,
                status=MeetingIntelligenceStatus.QUEUED,
            )
        )
    else:
        run.status = MeetingIntelligenceStatus.QUEUED
        run.error = None
    db.commit()
    response = IntelligenceGenerateResponse(job_id=job.job_id, meeting_id=meeting_id, status=MeetingIntelligenceStatus.QUEUED)
    return _success(response.model_dump(mode="json"), 202)


@router.get("/meetings/{meeting_id}/intelligence", summary="Get latest meeting intelligence")
def read_intelligence(meeting_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    intelligence = get_intelligence(db, meeting_id)
    if intelligence is None:
        return _error("INTELLIGENCE_NOT_FOUND", "Meeting intelligence is not available", 404)
    return _success(_intelligence_data(intelligence))


@router.get("/meetings/{meeting_id}/intelligence/status", summary="Get meeting intelligence status")
def read_intelligence_status(meeting_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    run, event_count = get_intelligence_status(db, meeting_id)
    if run is None:
        response = MeetingIntelligenceStatusRead(
            status=MeetingIntelligenceStatus.NOT_STARTED,
            provider=None,
            model=None,
            generated_at=None,
            error=None,
            event_count=event_count,
        )
    else:
        response = MeetingIntelligenceStatusRead(
            status=run.status,
            provider=run.provider,
            model=run.provider_model,
            generated_at=run.completed_at,
            error=run.error,
            event_count=event_count,
        )
    return _success(response.model_dump(mode="json"))


@router.get("/meetings/{meeting_id}/intelligence/{intelligence_id}", summary="Get meeting intelligence by ID")
def read_intelligence_detail(meeting_id: str, intelligence_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    intelligence = get_intelligence(db, meeting_id)
    if intelligence is None or intelligence.id != intelligence_id:
        return _error("INTELLIGENCE_NOT_FOUND", "Meeting intelligence is not available", 404)
    return _success(_intelligence_data(intelligence))
