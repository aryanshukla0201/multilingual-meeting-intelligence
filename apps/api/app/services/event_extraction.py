from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.models.entities import EventType
from app.settings import settings


class EventExtractionError(RuntimeError):
    pass


class TranscriptExtractionInput(BaseModel):
    id: str
    speaker_id: str | None
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    text: str


class ExtractedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    title: str = Field(min_length=1, max_length=300)
    subject: str | None = Field(default=None, max_length=300)
    value: str | None = None
    transcript_segment_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class EventExtractionProvider(ABC):
    name: str

    @abstractmethod
    def extract_events(
        self,
        transcript_segments: list[TranscriptExtractionInput],
        meeting_context: str | None = None,
    ) -> list[ExtractedEvent]:
        raise NotImplementedError


class OpenAICompatibleEventExtractionProvider(EventExtractionProvider):
    name = "openai-compatible"

    def __init__(
        self,
        model: str = settings.event_extraction_model,
        base_url: str | None = settings.llm_base_url,
        api_key: str | None = settings.llm_api_key,
        temperature: float = settings.event_extraction_temperature,
        timeout: int = settings.event_extraction_timeout,
    ) -> None:
        self.model_name = model
        self.base_url = (base_url or "http://localhost:11434/v1").rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout

    def extract_events(
        self,
        transcript_segments: list[TranscriptExtractionInput],
        meeting_context: str | None = None,
    ) -> list[ExtractedEvent]:
        if not self.model_name:
            raise EventExtractionError("EVENT_EXTRACTION_MODEL is not configured")
        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "event_extraction_v1.txt"
        system_prompt = prompt_path.read_text(encoding="utf-8")
        user_payload = {
            "meeting_context": meeting_context,
            "transcript_segments": [segment.model_dump() for segment in transcript_segments],
        }
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
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(user_payload)},
                    ],
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            payload = json.loads(content)
            raw_events = payload.get("events", payload) if isinstance(payload, dict) else payload
            if not isinstance(raw_events, list):
                raise EventExtractionError("LLM returned an invalid event collection")
            return [ExtractedEvent.model_validate(event) for event in raw_events]
        except EventExtractionError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise EventExtractionError("LLM event extraction failed") from exc


def create_event_extraction_provider() -> EventExtractionProvider:
    if settings.event_extraction_provider.lower() in {"openai-compatible", "openai", "local"}:
        return OpenAICompatibleEventExtractionProvider()
    raise EventExtractionError(f"Unsupported event extraction provider: {settings.event_extraction_provider}")
