from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, Meeting, Transcript, TranscriptionStatus
from app.schemas.contracts import (
    JobStatusRead,
    TranscriptRead,
    TranscriptSegmentRead,
    TranscriptionJobResponse,
)
from app.services.jobs import JobQueueError, enqueue_transcription_job, get_job
from app.services.transcript_service import get_segment, get_transcript

router = APIRouter(prefix="/api", tags=["transcription"])


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


def _transcript_data(state) -> dict:
    payload = {**state.transcript.__dict__, "segments": state.segments}
    payload.pop("_sa_instance_state", None)
    return TranscriptRead.model_validate(payload).model_dump(mode="json")


@router.post("/meetings/{meeting_id}/transcribe", status_code=202, summary="Queue meeting transcription")
def queue_transcription(meeting_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    asset = db.scalar(
        select(MediaAsset)
        .where(MediaAsset.meeting_id == meeting_id)
        .order_by(MediaAsset.created_at, MediaAsset.id)
    )
    if asset is None:
        return _error("MEDIA_NOT_FOUND", "No media asset exists for this meeting", 404)
    try:
        job = enqueue_transcription_job(meeting_id, asset.id)
    except JobQueueError as exc:
        return _error("JOB_QUEUE_UNAVAILABLE", str(exc), 503)
    transcript = db.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))
    if transcript is None:
        db.add(
            Transcript(
                meeting_id=meeting_id,
                media_asset_id=asset.id,
                status=TranscriptionStatus.TRANSCRIPTION_QUEUED,
            )
        )
    else:
        transcript.media_asset_id = asset.id
        transcript.status = TranscriptionStatus.TRANSCRIPTION_QUEUED
        transcript.error = None
    meeting = db.get(Meeting, meeting_id)
    if meeting is not None:
        meeting.processing_status = TranscriptionStatus.TRANSCRIPTION_QUEUED.value
    db.commit()
    response = TranscriptionJobResponse(
        job_id=job.job_id,
        meeting_id=job.meeting_id,
        status=job.status,
    )
    return _success(response.model_dump(mode="json"), 202)


@router.get("/meetings/{meeting_id}/transcript", summary="Get ordered meeting transcript")
def read_transcript(meeting_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    state = get_transcript(db, meeting_id)
    if state is None:
        return _error("TRANSCRIPT_NOT_FOUND", "Transcript is not available", 404)
    return _success(_transcript_data(state))


@router.get("/meetings/{meeting_id}/transcript/{segment_id}", summary="Get one transcript segment")
def read_transcript_segment(
    meeting_id: str,
    segment_id: str,
    db: Session = Depends(get_db),
) -> JSONResponse:
    segment = get_segment(db, meeting_id, segment_id)
    if segment is None:
        return _error("TRANSCRIPT_SEGMENT_NOT_FOUND", "Transcript segment not found", 404)
    response = TranscriptSegmentRead.model_validate(segment)
    return _success(response.model_dump(mode="json"))


@router.get("/jobs/{job_id}", summary="Get transcription job status")
def read_job(job_id: str) -> JSONResponse:
    try:
        job = get_job(job_id)
    except Exception:
        return _error("JOB_QUEUE_UNAVAILABLE", "Job queue is unavailable", 503)
    if job is None:
        return _error("JOB_NOT_FOUND", "Job not found", 404)
    response = JobStatusRead(
        job_id=job.job_id,
        meeting_id=job.meeting_id,
        status=job.status,
        error=job.error,
    )
    return _success(response.model_dump(mode="json"))
