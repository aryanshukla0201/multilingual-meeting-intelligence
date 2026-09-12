from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import (
    DiarizationRun,
    DiarizationStatus,
    MediaAsset,
    Meeting,
    Speaker,
    SpeakerSegment,
)
from app.schemas.contracts import (
    DiarizationJobResponse,
    DiarizationStatusRead,
    SpeakerMappingUpdate,
    SpeakerRead,
    SpeakerSegmentRead,
)
from app.services.jobs import JobQueueError, enqueue_diarization_job
from app.services.speaker_service import (
    diarization_configuration_key,
    list_speaker_segments,
    list_speakers,
)
from app.services.diarization import PyannoteDiarizationProvider
from app.settings import settings

router = APIRouter(prefix="/api", tags=["speakers"])


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


@router.post("/meetings/{meeting_id}/diarize", status_code=202, summary="Queue speaker diarization")
def queue_diarization(meeting_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    meeting = db.get(Meeting, meeting_id)
    asset = db.scalar(
        select(MediaAsset)
        .where(MediaAsset.meeting_id == meeting_id)
        .order_by(MediaAsset.created_at, MediaAsset.id)
    )
    if meeting is None:
        return _error("MEETING_NOT_FOUND", "Meeting not found", 404)
    if asset is None:
        return _error("MEDIA_NOT_FOUND", "No media asset exists for this meeting", 404)
    try:
        job = enqueue_diarization_job(meeting_id, asset.id)
    except JobQueueError as exc:
        return _error("JOB_QUEUE_UNAVAILABLE", str(exc), 503)

    run = db.scalar(
        select(DiarizationRun).where(
            DiarizationRun.meeting_id == meeting_id,
            DiarizationRun.media_asset_id == asset.id,
        )
    )
    if run is None:
        db.add(
            DiarizationRun(
                meeting_id=meeting_id,
                media_asset_id=asset.id,
                provider=settings.diarization_provider,
                provider_model=settings.diarization_model,
                provider_device=settings.diarization_device,
                configuration_key=diarization_configuration_key(PyannoteDiarizationProvider()),
                status=DiarizationStatus.DIARIZATION_QUEUED,
            )
        )
    else:
        run.status = DiarizationStatus.DIARIZATION_QUEUED
        run.error = None
    meeting.processing_status = DiarizationStatus.DIARIZATION_QUEUED.value
    db.commit()
    response = DiarizationJobResponse(
        job_id=job.job_id,
        meeting_id=job.meeting_id,
        status=DiarizationStatus.DIARIZATION_QUEUED,
    )
    return _success(response.model_dump(mode="json"), 202)


@router.get("/meetings/{meeting_id}/diarization/status", summary="Get diarization status")
def get_diarization_status(meeting_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    run = db.scalar(
        select(DiarizationRun)
        .where(DiarizationRun.meeting_id == meeting_id)
        .order_by(DiarizationRun.created_at.desc())
    )
    response = DiarizationStatusRead(
        status=run.status if run is not None else DiarizationStatus.DIARIZATION_NOT_STARTED,
        error=run.error if run is not None else None,
    )
    return _success(response.model_dump(mode="json"))


@router.get("/meetings/{meeting_id}/speakers", summary="List meeting speakers")
def get_meeting_speakers(meeting_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    speakers = list_speakers(db, meeting_id)
    data = []
    for speaker in speakers:
        count, duration = db.execute(
            select(func.count(SpeakerSegment.id), func.coalesce(func.sum(SpeakerSegment.end_time - SpeakerSegment.start_time), 0.0)).where(
                SpeakerSegment.speaker_id == speaker.id
            )
        ).one()
        data.append(
            SpeakerRead.model_validate(
                {
                    **speaker.__dict__,
                    "segments_count": int(count),
                    "speaking_duration_seconds": float(duration),
                }
            ).model_dump(mode="json")
        )
    return _success(data)


@router.get("/meetings/{meeting_id}/speaker-segments", summary="List raw speaker segments")
def get_meeting_speaker_segments(meeting_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    speakers = {speaker.id: speaker for speaker in list_speakers(db, meeting_id)}
    data = []
    for segment in list_speaker_segments(db, meeting_id):
        speaker = speakers.get(segment.speaker_id)
        if speaker is None:
            continue
        data.append(
            SpeakerSegmentRead.model_validate(
                {
                    **segment.__dict__,
                    "speaker_label": speaker.label,
                    "speaker_display_name": speaker.display_name,
                }
            ).model_dump(mode="json")
        )
    return _success(data)


@router.patch("/meetings/{meeting_id}/speakers/{speaker_id}", summary="Map an anonymous speaker name")
def update_speaker(
    meeting_id: str,
    speaker_id: str,
    update: SpeakerMappingUpdate,
    db: Session = Depends(get_db),
) -> JSONResponse:
    speaker = db.scalar(
        select(Speaker).where(
            Speaker.meeting_id == meeting_id,
            or_(Speaker.id == speaker_id, Speaker.label == speaker_id),
        )
    )
    if speaker is None:
        return _error("SPEAKER_NOT_FOUND", "Speaker not found", 404)
    speaker.display_name = update.display_name
    db.commit()
    count, duration = db.execute(
        select(func.count(SpeakerSegment.id), func.coalesce(func.sum(SpeakerSegment.end_time - SpeakerSegment.start_time), 0.0)).where(
            SpeakerSegment.speaker_id == speaker.id
        )
    ).one()
    response = SpeakerRead.model_validate(
        {
            **speaker.__dict__,
            "segments_count": int(count),
            "speaking_duration_seconds": float(duration),
        }
    )
    return _success(response.model_dump(mode="json"))
