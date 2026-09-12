from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from redis import Redis

from app.models.entities import TranscriptionStatus
from app.settings import settings


class JobQueueError(RuntimeError):
    pass


@dataclass(frozen=True)
class JobStatus:
    job_id: str
    meeting_id: str
    status: str
    error: str | None = None


def _redis() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def enqueue_transcription_job(meeting_id: str, media_asset_id: str) -> JobStatus:
    job_id = str(uuid4())
    payload = {"kind": "transcription", "job_id": job_id, "meeting_id": meeting_id, "media_asset_id": media_asset_id}
    client = _redis()
    try:
        client.hset(
            f"meeting-intelligence:job:{job_id}",
            mapping={
                "job_id": job_id,
                "meeting_id": meeting_id,
                "status": TranscriptionStatus.TRANSCRIPTION_QUEUED.value,
            },
        )
        client.lpush(settings.transcription_queue, json.dumps(payload))
    except Exception as exc:
        raise JobQueueError("Transcription queue is unavailable") from exc
    finally:
        client.close()
    return JobStatus(job_id, meeting_id, TranscriptionStatus.TRANSCRIPTION_QUEUED.value)


def enqueue_diarization_job(meeting_id: str, media_asset_id: str) -> JobStatus:
    job_id = str(uuid4())
    payload = {"kind": "diarization", "job_id": job_id, "meeting_id": meeting_id, "media_asset_id": media_asset_id}
    client = _redis()
    try:
        client.hset(
            f"meeting-intelligence:job:{job_id}",
            mapping={
                "job_id": job_id,
                "meeting_id": meeting_id,
                "status": "DIARIZATION_QUEUED",
            },
        )
        client.lpush(settings.transcription_queue, json.dumps(payload))
    except Exception as exc:
        raise JobQueueError("Diarization queue is unavailable") from exc
    finally:
        client.close()
    return JobStatus(job_id, meeting_id, "DIARIZATION_QUEUED")


def enqueue_event_extraction_job(meeting_id: str) -> JobStatus:
    job_id = str(uuid4())
    payload = {"kind": "event_extraction", "job_id": job_id, "meeting_id": meeting_id}
    client = _redis()
    try:
        client.hset(
            f"meeting-intelligence:job:{job_id}",
            mapping={"job_id": job_id, "meeting_id": meeting_id, "status": "QUEUED"},
        )
        client.lpush(settings.transcription_queue, json.dumps(payload))
    except Exception as exc:
        raise JobQueueError("Event extraction queue is unavailable") from exc
    finally:
        client.close()
    return JobStatus(job_id, meeting_id, "QUEUED")


def enqueue_meeting_intelligence_job(meeting_id: str) -> JobStatus:
    job_id = str(uuid4())
    payload = {"kind": "meeting_intelligence", "job_id": job_id, "meeting_id": meeting_id}
    client = _redis()
    try:
        client.hset(
            f"meeting-intelligence:job:{job_id}",
            mapping={"job_id": job_id, "meeting_id": meeting_id, "status": "QUEUED"},
        )
        client.lpush(settings.transcription_queue, json.dumps(payload))
    except Exception as exc:
        raise JobQueueError("Meeting intelligence queue is unavailable") from exc
    finally:
        client.close()
    return JobStatus(job_id, meeting_id, "QUEUED")


def update_job(job_id: str, meeting_id: str, status: str | TranscriptionStatus, error: str | None = None) -> None:
    client = _redis()
    status_value = status.value if isinstance(status, TranscriptionStatus) else status
    values = {"job_id": job_id, "meeting_id": meeting_id, "status": status_value}
    if error:
        values["error"] = error
    try:
        client.hset(f"meeting-intelligence:job:{job_id}", mapping=values)
    finally:
        client.close()


def get_job(job_id: str) -> JobStatus | None:
    client = _redis()
    try:
        values = client.hgetall(f"meeting-intelligence:job:{job_id}")
    finally:
        client.close()
    if not values:
        return None
    return JobStatus(
        job_id=values["job_id"],
        meeting_id=values["meeting_id"],
        status=values["status"],
        error=values.get("error"),
    )
