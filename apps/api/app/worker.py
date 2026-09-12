from __future__ import annotations

import json
import logging

from redis import Redis

from app.db.session import SessionLocal
from app.models.entities import TranscriptionStatus
from app.services.asr import ASRProviderError, create_asr_provider
from app.services.diarization import DiarizationProviderError, create_diarization_provider
from app.services.event_extraction import EventExtractionError, create_event_extraction_provider
from app.services.event_service import EventServiceError, extract_meeting_events
from app.services.intelligence import IntelligenceError, create_meeting_intelligence_provider, generate_meeting_intelligence
from app.services.jobs import update_job
from app.services.storage import create_storage_provider
from app.services.speaker_service import SpeakerProcessingError, diarize_media_asset
from app.services.transcript_service import TranscriptionError, transcribe_media_asset
from app.settings import settings

logger = logging.getLogger(__name__)


def process_transcription_job(payload: dict) -> None:
    job_id = payload["job_id"]
    meeting_id = payload["meeting_id"]
    media_asset_id = payload.get("media_asset_id")
    kind = payload.get("kind", "transcription")
    update_job(
        job_id,
        meeting_id,
        "PROCESSING" if kind in {"event_extraction", "meeting_intelligence"} else (
            "DIARIZATION_PROCESSING" if kind == "diarization" else TranscriptionStatus.TRANSCRIBING
        ),
    )
    db = SessionLocal()
    try:
        if kind == "meeting_intelligence":
            generate_meeting_intelligence(db, meeting_id, create_meeting_intelligence_provider())
            update_job(job_id, meeting_id, "COMPLETED")
        elif kind == "event_extraction":
            extract_meeting_events(db, meeting_id, create_event_extraction_provider())
            update_job(job_id, meeting_id, "COMPLETED")
        elif kind == "diarization":
            diarize_media_asset(db, media_asset_id, create_diarization_provider(), create_storage_provider())
            update_job(job_id, meeting_id, "DIARIZATION_COMPLETED")
        else:
            transcribe_media_asset(db, media_asset_id, create_asr_provider(), create_storage_provider())
            update_job(job_id, meeting_id, TranscriptionStatus.TRANSCRIBED)
    except (
        TranscriptionError,
        ASRProviderError,
        SpeakerProcessingError,
        DiarizationProviderError,
        EventExtractionError,
        EventServiceError,
        IntelligenceError,
    ) as exc:
        failed_status = (
            "FAILED" if kind in {"event_extraction", "meeting_intelligence"} else
            ("DIARIZATION_FAILED" if kind == "diarization" else TranscriptionStatus.TRANSCRIPTION_FAILED)
        )
        update_job(job_id, meeting_id, failed_status, str(exc))
        logger.error(
            "transcription job failed meeting_id=%s media_asset_id=%s job_id=%s status=%s error=%s",
            meeting_id,
            media_asset_id,
            job_id,
            failed_status.value if isinstance(failed_status, TranscriptionStatus) else failed_status,
            str(exc),
        )
    except Exception as exc:
        update_job(job_id, meeting_id, "FAILED" if kind in {"event_extraction", "meeting_intelligence"} else TranscriptionStatus.TRANSCRIPTION_FAILED, "Unexpected worker failure")
        logger.exception(
            "transcription worker failure meeting_id=%s media_asset_id=%s job_id=%s error=%s",
            meeting_id,
            media_asset_id,
            job_id,
            str(exc),
        )
    finally:
        db.close()


def run_worker() -> None:
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    logger.info("transcription worker started queue=%s", settings.transcription_queue)
    try:
        while True:
            item = client.brpop(settings.transcription_queue, timeout=5)
            if item is None:
                continue
            _, raw_payload = item
            process_transcription_job(json.loads(raw_payload))
    finally:
        client.close()


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    run_worker()
