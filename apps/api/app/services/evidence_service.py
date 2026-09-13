from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (
    Evidence,
    EvidenceType,
    Event,
    EventEvidence,
    Meeting,
    MediaAsset,
    Speaker,
    TranscriptSegment,
)


class EvidenceServiceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _validate_timestamp_range(start_time: float | None, end_time: float | None) -> None:
    if start_time is None and end_time is None:
        return
    if start_time is not None and start_time < 0:
        raise EvidenceServiceError("INVALID_TIMESTAMP", "Evidence start_time must be non-negative", 422)
    if end_time is not None and end_time < 0:
        raise EvidenceServiceError("INVALID_TIMESTAMP", "Evidence end_time must be non-negative", 422)
    if start_time is not None and end_time is not None and start_time > end_time:
        raise EvidenceServiceError("INVALID_TIMESTAMP", "Invalid timestamp range: start_time must be <= end_time", 422)


def _validate_meeting_ownership(
    db: Session,
    meeting_id: str,
    transcript_segment_id: str | None,
    speaker_id: str | None,
    media_asset_id: str | None,
) -> None:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise EvidenceServiceError("MEETING_NOT_FOUND", "Meeting not found", 404)

    segment: TranscriptSegment | None = None
    if transcript_segment_id is not None:
        segment = db.get(TranscriptSegment, transcript_segment_id)
        if segment is None:
            raise EvidenceServiceError("TRANSCRIPT_SEGMENT_NOT_FOUND", "Transcript segment not found", 404)
        if segment.meeting_id != meeting_id:
            raise EvidenceServiceError("CROSS_MEETING_EVIDENCE", "Transcript segment must belong to the same meeting", 422)
        if media_asset_id is not None and segment.media_asset_id != media_asset_id:
            raise EvidenceServiceError("INVALID_MEDIA_ASSET", "Transcript segment does not match the supplied media asset", 422)

    if speaker_id is not None:
        speaker = db.get(Speaker, speaker_id)
        if speaker is None:
            raise EvidenceServiceError("SPEAKER_NOT_FOUND", "Speaker not found", 404)
        if speaker.meeting_id != meeting_id:
            raise EvidenceServiceError("INVALID_SPEAKER", "speaker does not belong to the meeting", 422)
        if segment is not None and segment.speaker_id and speaker_id != segment.speaker_id:
            raise EvidenceServiceError("INVALID_SPEAKER", "speaker does not belong to the transcript segment", 422)

    if media_asset_id is not None:
        media_asset = db.get(MediaAsset, media_asset_id)
        if media_asset is None:
            raise EvidenceServiceError("MEDIA_ASSET_NOT_FOUND", "Media asset not found", 404)
        if media_asset.meeting_id != meeting_id:
            raise EvidenceServiceError("CROSS_MEETING_EVIDENCE", "Media asset must belong to the same meeting", 422)


def _validate_event_relation(db: Session, meeting_id: str, event_id: str | None) -> None:
    if event_id is None:
        return
    event = db.get(Event, event_id)
    if event is None:
        raise EvidenceServiceError("EVENT_NOT_FOUND", "Event not found", 404)
    if event.meeting_id != meeting_id:
        raise EvidenceServiceError("CROSS_MEETING_EVIDENCE", "Event must belong to the same meeting", 422)


def create_evidence(
    db: Session,
    *,
    meeting_id: str,
    evidence_type: EvidenceType,
    transcript_segment_id: str | None = None,
    media_asset_id: str | None = None,
    speaker_id: str | None = None,
    start_time: float | None = None,
    end_time: float | None = None,
    content: str | None = None,
    confidence: float | None = None,
    metadata: dict[str, Any] | None = None,
    event_id: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
) -> Evidence:
    _validate_meeting_ownership(db, meeting_id, transcript_segment_id, speaker_id, media_asset_id)
    _validate_event_relation(db, meeting_id, event_id)
    _validate_timestamp_range(start_time, end_time)

    segment: TranscriptSegment | None = None
    if evidence_type == EvidenceType.TRANSCRIPT:
        if transcript_segment_id is None:
            raise EvidenceServiceError("INVALID_EVIDENCE", "Transcript evidence requires a transcript segment", 422)
        segment = db.get(TranscriptSegment, transcript_segment_id)
        if segment is None:
            raise EvidenceServiceError("TRANSCRIPT_SEGMENT_NOT_FOUND", "Transcript segment not found", 404)
        if start_time is None:
            start_time = segment.start_time
        if end_time is None:
            end_time = segment.end_time
        if not (segment.start_time <= start_time <= segment.end_time and segment.start_time <= end_time <= segment.end_time):
            raise EvidenceServiceError("INVALID_TIMESTAMP", "Evidence timestamps must fall within transcript segment bounds", 422)
        if speaker_id is not None and segment.speaker_id and speaker_id != segment.speaker_id:
            raise EvidenceServiceError("INVALID_SPEAKER", "speaker does not belong to the transcript segment", 422)
        if content is None:
            content = segment.text

    if confidence is not None and (confidence < 0 or confidence > 1):
        raise EvidenceServiceError("INVALID_CONFIDENCE", "Evidence confidence must be between 0 and 1", 422)

    row = Evidence(
        meeting_id=meeting_id,
        evidence_type=evidence_type,
        source_type=source_type,
        source_id=source_id,
        media_asset_id=media_asset_id,
        transcript_segment_id=transcript_segment_id,
        speaker_id=speaker_id,
        start_time=start_time,
        end_time=end_time,
        content=content,
        confidence=confidence,
        metadata_json=metadata,
    )
    db.add(row)
    db.flush()

    if event_id is not None:
        event = db.get(Event, event_id)
        if event is None:
            raise EvidenceServiceError("EVENT_NOT_FOUND", "Event not found", 404)
        db.add(
            EventEvidence(
                event_id=event.id,
                evidence_id=row.id,
                transcript_segment_id=transcript_segment_id,
                evidence_start=start_time if start_time is not None else row.start_time,
                evidence_end=end_time if end_time is not None else row.end_time,
                relevance=confidence,
            )
        )
        db.flush()
    return row


def list_meeting_evidence(
    db: Session,
    meeting_id: str,
    *,
    evidence_type: EvidenceType | None = None,
    speaker_id: str | None = None,
    transcript_segment_id: str | None = None,
    event_id: str | None = None,
) -> list[Evidence]:
    statement = select(Evidence).where(Evidence.meeting_id == meeting_id)
    if evidence_type is not None:
        statement = statement.where(Evidence.evidence_type == evidence_type)
    if speaker_id is not None:
        statement = statement.where(Evidence.speaker_id == speaker_id)
    if transcript_segment_id is not None:
        statement = statement.where(Evidence.transcript_segment_id == transcript_segment_id)
    if event_id is not None:
        subset = (
            select(EventEvidence.evidence_id)
            .where(EventEvidence.event_id == event_id)
            .where(EventEvidence.evidence_id.isnot(None))
        )
        statement = statement.where(Evidence.id.in_(subset))
    return list(db.scalars(statement.order_by(Evidence.start_time, Evidence.id)).all())


def get_evidence(db: Session, meeting_id: str, evidence_id: str) -> Evidence | None:
    return db.scalar(select(Evidence).where(Evidence.meeting_id == meeting_id, Evidence.id == evidence_id))


def get_evidence_for_event(db: Session, event_id: str) -> list[Evidence]:
    return list(
        db.scalars(
            select(Evidence)
            .join(EventEvidence, EventEvidence.evidence_id == Evidence.id)
            .where(EventEvidence.event_id == event_id)
            .order_by(Evidence.start_time, Evidence.id)
        ).all()
    )


def resolve_transcript_evidence(db: Session, evidence_id: str) -> tuple[Evidence, TranscriptSegment | None] | None:
    evidence = db.get(Evidence, evidence_id)
    if evidence is None:
        return None
    segment = db.get(TranscriptSegment, evidence.transcript_segment_id) if evidence.transcript_segment_id else None
    return evidence, segment


def validate_meeting_evidence(db: Session, meeting_id: str, evidence_id: str) -> Evidence:
    evidence = get_evidence(db, meeting_id, evidence_id)
    if evidence is None:
        raise EvidenceServiceError("EVIDENCE_NOT_FOUND", "Evidence not found", 404)
    return evidence
