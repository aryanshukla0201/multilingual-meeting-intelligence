from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import EvidenceType, EventEvidence, Speaker
from app.schemas.contracts import EvidenceRead, EventEvidenceRead
from app.services.evidence_service import get_evidence, get_evidence_for_event, list_meeting_evidence, validate_meeting_evidence

router = APIRouter(prefix="/api", tags=["evidence"])


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


@router.get("/meetings/{meeting_id}/evidence", summary="List meeting evidence")
def read_meeting_evidence(
    meeting_id: str,
    evidence_type: EvidenceType | None = Query(default=None),
    speaker_id: str | None = Query(default=None),
    transcript_segment_id: str | None = Query(default=None),
    event_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    evidence = list_meeting_evidence(
        db,
        meeting_id,
        evidence_type=evidence_type,
        speaker_id=speaker_id,
        transcript_segment_id=transcript_segment_id,
        event_id=event_id,
    )
    payload = []
    for row in evidence:
        speaker = row.speaker_id and db.get(Speaker, row.speaker_id)
        payload.append(
            EvidenceRead(
                id=row.id,
                meeting_id=row.meeting_id,
                evidence_type=row.evidence_type,
                source_type=row.source_type,
                source_id=row.source_id,
                media_asset_id=row.media_asset_id,
                transcript_segment_id=row.transcript_segment_id,
                speaker_id=row.speaker_id,
                speaker_label=speaker.label if speaker else None,
                start_time=row.start_time,
                end_time=row.end_time,
                content=row.content,
                confidence=row.confidence,
                metadata_json=row.metadata_json,
                created_at=row.created_at,
                updated_at=row.updated_at,
            ).model_dump(mode="json")
        )
    return _success(payload)


@router.get("/meetings/{meeting_id}/evidence/{evidence_id}", summary="Get meeting evidence detail")
def read_meeting_evidence_detail(meeting_id: str, evidence_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    row = validate_meeting_evidence(db, meeting_id, evidence_id)
    speaker = row.speaker_id and db.get(Speaker, row.speaker_id)
    response = EvidenceRead(
        id=row.id,
        meeting_id=row.meeting_id,
        evidence_type=row.evidence_type,
        source_type=row.source_type,
        source_id=row.source_id,
        media_asset_id=row.media_asset_id,
        transcript_segment_id=row.transcript_segment_id,
        speaker_id=row.speaker_id,
        speaker_label=speaker.label if speaker else None,
        start_time=row.start_time,
        end_time=row.end_time,
        content=row.content,
        confidence=row.confidence,
        metadata_json=row.metadata_json,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
    return _success(response.model_dump(mode="json"))


@router.get("/meetings/{meeting_id}/events/{event_id}/evidence", summary="Get evidence for an event")
def read_event_evidence(meeting_id: str, event_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    from app.services.event_service import get_event_evidence

    rows = get_event_evidence(db, event_id)
    payload = []
    for bridge, segment in rows:
        if segment.meeting_id != meeting_id:
            return _error("CROSS_MEETING_EVIDENCE", "Event evidence does not belong to the requested meeting", 422)
        speaker = segment.speaker_id and db.get(Speaker, segment.speaker_id)
        payload.append(
            EventEvidenceRead(
                id=bridge.id,
                evidence_id=bridge.evidence_id,
                transcript_segment_id=segment.id,
                start_time=bridge.evidence_start,
                end_time=bridge.evidence_end,
                text=segment.text,
                speaker_id=segment.speaker_id,
                speaker_label=speaker.label if speaker else None,
            ).model_dump(mode="json")
        )
    return _success(payload)
