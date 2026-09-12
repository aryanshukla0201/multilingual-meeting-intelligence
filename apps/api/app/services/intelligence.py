from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.entities import (
    Event,
    EventEvidence,
    EventExtractionRun,
    EventExtractionStatus,
    Meeting,
    MeetingIntelligence,
    MeetingIntelligenceRun,
    MeetingIntelligenceStatus,
    Speaker,
)
from app.settings import settings


class IntelligenceError(RuntimeError):
    pass


class IntelligenceEventInput(BaseModel):
    id: str
    event_type: str
    title: str | None
    subject: str | None
    value: str | None
    speaker_id: str | None
    speaker_label: str | None
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_count: int = Field(ge=0)


class IntelligenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1)
    event_ids: list[str] = Field(min_length=1)
    status: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class MeetingIntelligenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    decisions: list[IntelligenceItem] = []
    commitments: list[IntelligenceItem] = []
    actions: list[IntelligenceItem] = []
    risks: list[IntelligenceItem] = []
    questions: list[IntelligenceItem] = []
    proposals: list[IntelligenceItem] = []
    claims: list[IntelligenceItem] = []
    constraints: list[IntelligenceItem] = []
    assumptions: list[IntelligenceItem] = []
    highlights: list[IntelligenceItem] = []
    open_items: list[IntelligenceItem] = []


class MeetingIntelligenceProvider(ABC):
    name: str

    @abstractmethod
    def synthesize(
        self,
        events: list[IntelligenceEventInput],
        meeting_context: str | None = None,
    ) -> MeetingIntelligenceOutput:
        raise NotImplementedError


class OpenAICompatibleMeetingIntelligenceProvider(MeetingIntelligenceProvider):
    name = "openai-compatible"

    def __init__(
        self,
        model: str = settings.meeting_intelligence_model,
        base_url: str | None = settings.llm_base_url,
        api_key: str | None = settings.llm_api_key,
        temperature: float = settings.meeting_intelligence_temperature,
        timeout: int = settings.meeting_intelligence_timeout,
    ) -> None:
        self.model_name = model
        self.base_url = (base_url or "http://localhost:11434/v1").rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout

    def synthesize(
        self,
        events: list[IntelligenceEventInput],
        meeting_context: str | None = None,
    ) -> MeetingIntelligenceOutput:
        if not self.model_name:
            raise IntelligenceError("MEETING_INTELLIGENCE_MODEL is not configured")
        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "meeting_intelligence_v1.txt"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model_name,
                    "temperature": self.temperature,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": prompt_path.read_text(encoding="utf-8")},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "meeting_context": meeting_context,
                                    "events": [event.model_dump() for event in events],
                                }
                            ),
                        },
                    ],
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = json.loads(response.json()["choices"][0]["message"]["content"])
            return MeetingIntelligenceOutput.model_validate(payload)
        except IntelligenceError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise IntelligenceError("Meeting intelligence provider failed") from exc


def create_meeting_intelligence_provider() -> MeetingIntelligenceProvider:
    if settings.meeting_intelligence_provider.lower() in {"openai-compatible", "openai", "local"}:
        return OpenAICompatibleMeetingIntelligenceProvider()
    raise IntelligenceError(f"Unsupported meeting intelligence provider: {settings.meeting_intelligence_provider}")


@dataclass(frozen=True)
class IntelligenceState:
    run: MeetingIntelligenceRun
    intelligence: MeetingIntelligence | None
    reused: bool = False


def intelligence_configuration_key(provider: MeetingIntelligenceProvider) -> str:
    values = {
        "provider": getattr(provider, "name", provider.__class__.__name__),
        "model": getattr(provider, "model_name", None),
        "temperature": getattr(provider, "temperature", None),
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()


def _event_inputs(db: Session, meeting_id: str, extraction_run_id: str) -> list[IntelligenceEventInput]:
    events = list(
        db.scalars(
            select(Event)
            .where(Event.meeting_id == meeting_id, Event.extraction_run_id == extraction_run_id)
            .order_by(Event.start_time, Event.id)
        ).all()
    )
    speakers = {speaker.id: speaker for speaker in db.scalars(select(Speaker).where(Speaker.meeting_id == meeting_id)).all()}
    return [
        IntelligenceEventInput(
            id=event.id,
            event_type=event.event_type,
            title=event.title,
            subject=event.subject,
            value=event.value,
            speaker_id=event.speaker_id,
            speaker_label=speakers.get(event.speaker_id).label if event.speaker_id in speakers else None,
            start_time=event.start_time,
            end_time=event.end_time,
            confidence=event.confidence_score,
            evidence_count=int(db.scalar(select(func.count(EventEvidence.id)).where(EventEvidence.event_id == event.id)) or 0),
        )
        for event in events
    ]


def generate_meeting_intelligence(
    db: Session,
    meeting_id: str,
    provider: MeetingIntelligenceProvider,
) -> IntelligenceState:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise IntelligenceError("MEETING_NOT_FOUND")
    extraction_run = db.scalar(
        select(EventExtractionRun)
        .where(
            EventExtractionRun.meeting_id == meeting_id,
            EventExtractionRun.status == EventExtractionStatus.COMPLETED,
        )
        .order_by(EventExtractionRun.completed_at.desc())
    )
    if extraction_run is None:
        raise IntelligenceError("Event extraction must be completed before meeting intelligence can be generated")

    configuration_key = intelligence_configuration_key(provider)
    run = db.scalar(
        select(MeetingIntelligenceRun).where(
            MeetingIntelligenceRun.meeting_id == meeting_id,
            MeetingIntelligenceRun.configuration_key == configuration_key,
        )
    )
    if run is not None and run.status == MeetingIntelligenceStatus.COMPLETED:
        intelligence = db.scalar(
            select(MeetingIntelligence).where(MeetingIntelligence.generation_run_id == run.id)
        )
        return IntelligenceState(run, intelligence, reused=True)
    if run is None:
        run = MeetingIntelligenceRun(
            meeting_id=meeting_id,
            provider=getattr(provider, "name", provider.__class__.__name__),
            provider_model=getattr(provider, "model_name", None),
            configuration_key=configuration_key,
            status=MeetingIntelligenceStatus.QUEUED,
        )
        db.add(run)
        db.flush()
    else:
        run.status = MeetingIntelligenceStatus.QUEUED
        run.error = None
    db.commit()

    run.status = MeetingIntelligenceStatus.PROCESSING
    run.started_at = datetime.now(timezone.utc)
    db.commit()
    started = time.monotonic()
    try:
        inputs = _event_inputs(db, meeting_id, extraction_run.id)
        output = provider.synthesize(inputs, meeting.title)
        valid_ids = {event.id for event in db.scalars(select(Event).where(Event.meeting_id == meeting_id, Event.extraction_run_id == extraction_run.id)).all()}
        _validate_output(output, valid_ids)
        intelligence = MeetingIntelligence(
            meeting_id=meeting_id,
            generation_run_id=run.id,
            provider=run.provider,
            provider_model=run.provider_model,
            configuration_key=configuration_key,
            status=MeetingIntelligenceStatus.COMPLETED,
            summary=output.summary,
            sections=output.model_dump(mode="json", exclude={"summary"}),
            generated_at=datetime.now(timezone.utc),
        )
        db.add(intelligence)
        run.status = MeetingIntelligenceStatus.COMPLETED
        run.completed_at = datetime.now(timezone.utc)
        run.error = None
        db.commit()
        db.refresh(intelligence)
        return IntelligenceState(run, intelligence)
    except IntelligenceError as exc:
        _mark_failed(db, run, str(exc))
        raise
    except Exception as exc:
        _mark_failed(db, run, "Unexpected meeting intelligence failure")
        raise IntelligenceError("Unexpected meeting intelligence failure") from exc


def _validate_output(output: MeetingIntelligenceOutput, valid_ids: set[str]) -> None:
    for section_name in (
        "decisions", "commitments", "actions", "risks", "questions", "proposals",
        "claims", "constraints", "assumptions", "highlights", "open_items",
    ):
        for item in getattr(output, section_name):
            if not set(item.event_ids).issubset(valid_ids):
                raise IntelligenceError(f"Invalid event reference in {section_name}")


def _mark_failed(db: Session, run: MeetingIntelligenceRun, error: str) -> None:
    db.rollback()
    run.status = MeetingIntelligenceStatus.FAILED
    run.error = error
    db.commit()


def get_intelligence(db: Session, meeting_id: str) -> MeetingIntelligence | None:
    return db.scalar(
        select(MeetingIntelligence)
        .where(MeetingIntelligence.meeting_id == meeting_id)
        .order_by(MeetingIntelligence.generated_at.desc())
    )


def get_intelligence_status(db: Session, meeting_id: str) -> tuple[MeetingIntelligenceRun | None, int]:
    run = db.scalar(
        select(MeetingIntelligenceRun)
        .where(MeetingIntelligenceRun.meeting_id == meeting_id)
        .order_by(MeetingIntelligenceRun.created_at.desc())
    )
    if run is None:
        return None, 0
    count = db.scalar(
        select(func.count(Event.id)).where(Event.meeting_id == meeting_id)
    ) or 0
    return run, int(count)
