from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.entities import (
    DiarizationRun,
    DiarizationStatus,
    MediaAsset,
    Meeting,
    MeetingStatus,
    Speaker,
    SpeakerSegment,
    TranscriptSegment,
)
from app.services.diarization import (
    DiarizationProvider,
    DiarizationProviderError,
    DiarizationResult,
    validate_diarization_result,
)
from app.services.media_audio import AudioPreparationError, audio_path_for_asset
from app.services.storage import StorageProvider
from app.settings import settings

logger = logging.getLogger(__name__)


class SpeakerProcessingError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class SpeakerState:
    run: DiarizationRun
    speakers: list[Speaker]
    segments: list[SpeakerSegment]
    reused: bool = False


def diarization_configuration_key(provider: DiarizationProvider) -> str:
    values = {
        "provider": getattr(provider, "name", provider.__class__.__name__),
        "model": getattr(provider, "model_name", None),
        "device": getattr(provider, "requested_device", None),
        "min_speakers": getattr(provider, "min_speakers", None),
        "max_speakers": getattr(provider, "max_speakers", None),
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def align_transcript_segments(
    db: Session,
    meeting_id: str,
    media_asset_id: str,
    raw_segments: list[SpeakerSegment],
) -> int:
    transcript_segments = list(
        db.scalars(
            select(TranscriptSegment)
            .where(
                TranscriptSegment.meeting_id == meeting_id,
                TranscriptSegment.media_asset_id == media_asset_id,
            )
            .order_by(TranscriptSegment.sequence, TranscriptSegment.start_time)
        ).all()
    )
    assigned = 0
    for transcript_segment in transcript_segments:
        candidates = [
            raw_segment
            for raw_segment in raw_segments
            if _overlap(
                transcript_segment.start_time,
                transcript_segment.end_time,
                raw_segment.start_time,
                raw_segment.end_time,
            )
            > 0
        ]
        if not candidates:
            transcript_segment.speaker_id = None
            continue
        winner = max(
            candidates,
            key=lambda candidate: (
                _overlap(
                    transcript_segment.start_time,
                    transcript_segment.end_time,
                    candidate.start_time,
                    candidate.end_time,
                ),
                candidate.confidence if candidate.confidence is not None else -1.0,
            ),
        )
        transcript_segment.speaker_id = winner.speaker_id
        assigned += 1
    return assigned


def diarize_media_asset(
    db: Session,
    media_asset_id: str,
    provider: DiarizationProvider,
    storage: StorageProvider,
) -> SpeakerState:
    asset = db.get(MediaAsset, media_asset_id)
    if asset is None:
        raise SpeakerProcessingError("MEDIA_NOT_FOUND", "Media asset not found", 404)
    meeting = db.get(Meeting, asset.meeting_id)
    if meeting is None:
        raise SpeakerProcessingError("MEETING_NOT_FOUND", "Meeting not found", 404)

    configuration_key = diarization_configuration_key(provider)
    run = db.scalar(
        select(DiarizationRun).where(
            DiarizationRun.media_asset_id == asset.id,
            DiarizationRun.configuration_key == configuration_key,
        )
    )
    if run is not None and run.status == DiarizationStatus.DIARIZATION_COMPLETED:
        speakers = list(
            db.scalars(
                select(Speaker).where(
                    Speaker.meeting_id == meeting.id,
                    Speaker.media_asset_id == asset.id,
                )
            ).all()
        )
        segments = list(
            db.scalars(
                select(SpeakerSegment)
                .where(SpeakerSegment.media_asset_id == asset.id)
                .order_by(SpeakerSegment.start_time, SpeakerSegment.id)
            ).all()
        )
        return SpeakerState(run, speakers, segments, reused=True)

    if run is None:
        run = DiarizationRun(
            meeting_id=meeting.id,
            media_asset_id=asset.id,
            provider=getattr(provider, "name", provider.__class__.__name__),
            provider_model=getattr(provider, "model_name", None),
            provider_device=getattr(provider, "requested_device", None),
            configuration_key=configuration_key,
            status=DiarizationStatus.DIARIZATION_QUEUED,
        )
        db.add(run)
        db.flush()
    else:
        run.status = DiarizationStatus.DIARIZATION_QUEUED
        run.error = None
    meeting.processing_status = DiarizationStatus.DIARIZATION_QUEUED.value
    db.commit()

    started = time.monotonic()
    run.status = DiarizationStatus.DIARIZATION_PROCESSING
    meeting.processing_status = DiarizationStatus.DIARIZATION_PROCESSING.value
    meeting.status = MeetingStatus.DIARIZING
    db.commit()
    try:
        with audio_path_for_asset(asset, storage, settings.ffmpeg_path) as audio_path:
            result = provider.diarize(asset, audio_path)
        validate_diarization_result(result)
        if not result.segments:
            raise SpeakerProcessingError("EMPTY_DIARIZATION", "Diarization returned no speaker segments", 422)
        speakers, raw_segments = _persist_result(
            db, meeting, asset, run, result, configuration_key
        )
        align_transcript_segments(db, meeting.id, asset.id, raw_segments)
        run.status = DiarizationStatus.DIARIZATION_COMPLETED
        run.error = None
        meeting.processing_status = DiarizationStatus.DIARIZATION_COMPLETED.value
        db.commit()
        logger.info(
            "diarization completed meeting_id=%s media_asset_id=%s provider=%s model=%s duration=%s status=%s",
            meeting.id,
            asset.id,
            result.provider,
            result.model,
            result.processing_duration,
            DiarizationStatus.DIARIZATION_COMPLETED.value,
        )
        return SpeakerState(run, speakers, raw_segments)
    except (AudioPreparationError, DiarizationProviderError, SpeakerProcessingError) as exc:
        _mark_failed(db, run, meeting, str(exc))
        logger.error(
            "diarization failed meeting_id=%s media_asset_id=%s provider=%s model=%s duration=%s status=%s error=%s",
            meeting.id,
            asset.id,
            getattr(provider, "name", provider.__class__.__name__),
            getattr(provider, "model_name", None),
            time.monotonic() - started,
            DiarizationStatus.DIARIZATION_FAILED.value,
            str(exc),
        )
        if isinstance(exc, SpeakerProcessingError):
            raise
        raise SpeakerProcessingError("DIARIZATION_FAILED", str(exc)) from exc
    except Exception as exc:
        _mark_failed(db, run, meeting, "Unexpected diarization failure")
        logger.exception(
            "diarization failed meeting_id=%s media_asset_id=%s status=%s",
            meeting.id,
            asset.id,
            DiarizationStatus.DIARIZATION_FAILED.value,
        )
        raise SpeakerProcessingError("DIARIZATION_FAILED", "Unexpected diarization failure") from exc


def _persist_result(
    db: Session,
    meeting: Meeting,
    asset: MediaAsset,
    run: DiarizationRun,
    result: DiarizationResult,
    configuration_key: str,
) -> tuple[list[Speaker], list[SpeakerSegment]]:
    db.execute(delete(SpeakerSegment).where(SpeakerSegment.media_asset_id == asset.id))
    labels = sorted({segment.speaker_id for segment in result.segments})
    speakers: dict[str, Speaker] = {}
    for label in labels:
        speaker = db.scalar(
            select(Speaker).where(
                Speaker.meeting_id == meeting.id,
                Speaker.media_asset_id == asset.id,
                Speaker.label == label,
            )
        )
        if speaker is None:
            speaker = Speaker(
                meeting_id=meeting.id,
                media_asset_id=asset.id,
                label=label,
                provider=result.provider,
                provider_model=result.model,
                configuration_key=configuration_key,
            )
            db.add(speaker)
            db.flush()
        else:
            speaker.provider = result.provider
            speaker.provider_model = result.model
            speaker.configuration_key = configuration_key
        speakers[label] = speaker

    raw_segments: list[SpeakerSegment] = []
    for segment in result.segments:
        raw_segment = SpeakerSegment(
            meeting_id=meeting.id,
            media_asset_id=asset.id,
            speaker_id=speakers[segment.speaker_id].id,
            start_time=segment.start_time,
            end_time=segment.end_time,
            confidence=segment.confidence,
            provider=result.provider,
            provider_model=result.model,
        )
        db.add(raw_segment)
        raw_segments.append(raw_segment)
    db.flush()
    run.provider = result.provider
    run.provider_model = result.model
    run.provider_device = result.device
    return list(speakers.values()), raw_segments


def _mark_failed(db: Session, run: DiarizationRun, meeting: Meeting, error: str) -> None:
    db.rollback()
    run.status = DiarizationStatus.DIARIZATION_FAILED
    run.error = error
    meeting.processing_status = DiarizationStatus.DIARIZATION_FAILED.value
    db.commit()


def list_speakers(db: Session, meeting_id: str) -> list[Speaker]:
    return list(
        db.scalars(
            select(Speaker).where(Speaker.meeting_id == meeting_id).order_by(Speaker.label)
        ).all()
    )


def list_speaker_segments(db: Session, meeting_id: str) -> list[SpeakerSegment]:
    return list(
        db.scalars(
            select(SpeakerSegment)
            .where(SpeakerSegment.meeting_id == meeting_id)
            .order_by(SpeakerSegment.start_time, SpeakerSegment.id)
        ).all()
    )
