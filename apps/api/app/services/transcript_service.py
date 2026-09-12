from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.entities import (
    MediaAsset,
    Meeting,
    MeetingStatus,
    Transcript,
    TranscriptSegment,
    TranscriptionStatus,
)
from app.services.asr import ASRProvider, ASRProviderError, TranscriptResult
from app.services.media_audio import AudioPreparationError, audio_path_for_asset
from app.services.storage import StorageProvider
from app.settings import settings

logger = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class TranscriptState:
    transcript: Transcript
    segments: list[TranscriptSegment]
    reused: bool = False


def asr_configuration_key(provider: ASRProvider) -> str:
    values = {
        "provider": getattr(provider, "name", provider.__class__.__name__),
        "model": getattr(provider, "model_name", None),
        "device": getattr(provider, "requested_device", None),
        "compute_type": getattr(provider, "compute_type", None),
        "language": getattr(provider, "language", None),
        "beam_size": getattr(provider, "beam_size", None),
        "vad_filter": getattr(provider, "vad_filter", None),
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()


def _find_transcript(db: Session, meeting_id: str, media_asset_id: str) -> Transcript | None:
    return db.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))


def transcribe_media_asset(
    db: Session,
    media_asset_id: str,
    provider: ASRProvider,
    storage: StorageProvider,
) -> TranscriptState:
    asset = db.get(MediaAsset, media_asset_id)
    if asset is None:
        raise TranscriptionError("MEDIA_NOT_FOUND", "Media asset not found", 404)
    meeting = db.get(Meeting, asset.meeting_id)
    if meeting is None:
        raise TranscriptionError("MEETING_NOT_FOUND", "Meeting not found", 404)

    configuration_key = asr_configuration_key(provider)
    transcript = _find_transcript(db, meeting.id, asset.id)
    if (
        transcript is not None
        and transcript.status == TranscriptionStatus.TRANSCRIBED
        and transcript.media_asset_id == asset.id
        and transcript.configuration_key == configuration_key
    ):
        segments = list(
            db.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.transcript_id == transcript.id)
                .order_by(TranscriptSegment.sequence, TranscriptSegment.start_time)
            ).all()
        )
        return TranscriptState(transcript, segments, reused=True)

    if transcript is None:
        transcript = Transcript(
            meeting_id=meeting.id,
            media_asset_id=asset.id,
            status=TranscriptionStatus.TRANSCRIPTION_QUEUED,
        )
        db.add(transcript)
        db.flush()
    else:
        transcript.media_asset_id = asset.id
        transcript.configuration_key = configuration_key
        transcript.error = None
        transcript.status = TranscriptionStatus.TRANSCRIPTION_QUEUED
    meeting.processing_status = TranscriptionStatus.TRANSCRIPTION_QUEUED.value
    db.commit()

    started = time.monotonic()
    transcript.status = TranscriptionStatus.TRANSCRIBING
    meeting.processing_status = TranscriptionStatus.TRANSCRIBING.value
    db.commit()
    try:
        with audio_path_for_asset(asset, storage, settings.ffmpeg_path) as audio_path:
            result = provider.transcribe(asset, audio_path)
        if not result.segments or not result.text:
            raise TranscriptionError("EMPTY_TRANSCRIPT", "ASR returned an empty transcript", 422)
        _persist_result(db, transcript, meeting, asset, result, configuration_key, started)
        segments = list(
            db.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.transcript_id == transcript.id)
                .order_by(TranscriptSegment.sequence, TranscriptSegment.start_time)
            ).all()
        )
        logger.info(
            "transcription completed meeting_id=%s media_asset_id=%s provider=%s model=%s duration=%s status=%s",
            meeting.id,
            asset.id,
            result.provider,
            result.model,
            result.processing_duration,
            TranscriptionStatus.TRANSCRIBED.value,
        )
        return TranscriptState(transcript, segments)
    except (AudioPreparationError, ASRProviderError, TranscriptionError) as exc:
        _mark_failed(db, transcript, meeting, str(exc))
        logger.error(
            "transcription failed meeting_id=%s media_asset_id=%s provider=%s model=%s duration=%s status=%s error=%s",
            meeting.id,
            asset.id,
            getattr(provider, "name", provider.__class__.__name__),
            getattr(provider, "model_name", None),
            time.monotonic() - started,
            TranscriptionStatus.TRANSCRIPTION_FAILED.value,
            str(exc),
        )
        if isinstance(exc, TranscriptionError):
            raise
        raise TranscriptionError("TRANSCRIPTION_FAILED", str(exc)) from exc
    except Exception as exc:
        _mark_failed(db, transcript, meeting, "Unexpected transcription failure")
        logger.exception(
            "transcription failed meeting_id=%s media_asset_id=%s status=%s",
            meeting.id,
            asset.id,
            TranscriptionStatus.TRANSCRIPTION_FAILED.value,
        )
        raise TranscriptionError("TRANSCRIPTION_FAILED", "Unexpected transcription failure") from exc


def _persist_result(
    db: Session,
    transcript: Transcript,
    meeting: Meeting,
    asset: MediaAsset,
    result: TranscriptResult,
    configuration_key: str,
    started: float,
) -> None:
    db.execute(delete(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript.id))
    transcript.media_asset_id = asset.id
    transcript.text = result.text
    transcript.detected_language = result.language
    transcript.provider = result.provider
    transcript.provider_model = result.model
    transcript.provider_device = result.device
    transcript.processing_duration = result.processing_duration or (time.monotonic() - started)
    transcript.configuration_key = configuration_key
    transcript.status = TranscriptionStatus.TRANSCRIBED
    transcript.error = None
    meeting.processing_status = TranscriptionStatus.TRANSCRIBED.value
    meeting.processing_progress = 100.0
    meeting.status = MeetingStatus.PROCESSING
    for sequence, segment in enumerate(result.segments):
        db.add(
            TranscriptSegment(
                transcript_id=transcript.id,
                meeting_id=meeting.id,
                media_asset_id=asset.id,
                segment_index=sequence,
                sequence=sequence,
                start_time=segment.start_time,
                end_time=segment.end_time,
                text=segment.text,
                language=segment.language or result.language,
                confidence=segment.confidence,
            )
        )
    db.commit()
    db.refresh(transcript)


def _mark_failed(db: Session, transcript: Transcript, meeting: Meeting, error: str) -> None:
    db.rollback()
    transcript.status = TranscriptionStatus.TRANSCRIPTION_FAILED
    transcript.error = error
    meeting.processing_status = TranscriptionStatus.TRANSCRIPTION_FAILED.value
    db.commit()


def get_transcript(db: Session, meeting_id: str) -> TranscriptState | None:
    transcript = db.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))
    if transcript is None:
        return None
    segments = list(
        db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.transcript_id == transcript.id)
            .order_by(TranscriptSegment.sequence, TranscriptSegment.start_time)
        ).all()
    )
    return TranscriptState(transcript, segments)


def get_segment(db: Session, meeting_id: str, segment_id: str) -> TranscriptSegment | None:
    return db.scalar(
        select(TranscriptSegment).where(
            TranscriptSegment.id == segment_id,
            TranscriptSegment.meeting_id == meeting_id,
        )
    )
