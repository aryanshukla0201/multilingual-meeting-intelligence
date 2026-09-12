from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.models.entities import MediaAsset
from app.settings import settings


class DiarizationProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiarizationSegmentResult:
    speaker_id: str
    start_time: float
    end_time: float
    confidence: float | None = None


@dataclass(frozen=True)
class DiarizationResult:
    segments: list[DiarizationSegmentResult]
    provider: str
    model: str
    device: str
    processing_duration: float


class DiarizationProvider(ABC):
    name: str

    @abstractmethod
    def diarize(self, media_asset: MediaAsset, audio_path: Path) -> DiarizationResult:
        raise NotImplementedError


class PyannoteDiarizationProvider(DiarizationProvider):
    name = "pyannote"

    def __init__(
        self,
        model: str = settings.diarization_model,
        device: str = settings.diarization_device,
        min_speakers: int | None = settings.diarization_min_speakers,
        max_speakers: int | None = settings.diarization_max_speakers,
        auth_token: str | None = settings.diarization_auth_token,
    ) -> None:
        self.model_name = model
        self.requested_device = device
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.auth_token = auth_token
        self.actual_device = device

    def _load_pipeline(self):
        try:
            from pyannote.audio import Pipeline
        except ImportError as exc:
            raise DiarizationProviderError(
                "pyannote.audio is not installed; install apps/api/requirements-diarization.txt"
            ) from exc
        try:
            try:
                pipeline = Pipeline.from_pretrained(self.model_name, token=self.auth_token)
            except TypeError:
                pipeline = Pipeline.from_pretrained(self.model_name, use_auth_token=self.auth_token)
            if self.requested_device != "cpu":
                import torch

                pipeline.to(torch.device(self.requested_device))
            self.actual_device = self.requested_device
            return pipeline
        except Exception as exc:
            if self.requested_device == "cpu":
                raise DiarizationProviderError("Diarization model could not be initialized") from exc
            try:
                pipeline = Pipeline.from_pretrained(self.model_name, token=self.auth_token)
                self.actual_device = "cpu"
                return pipeline
            except Exception as fallback_exc:
                raise DiarizationProviderError("Diarization model could not be initialized") from fallback_exc

    @staticmethod
    def _anonymous_id(label: str, assigned: dict[str, str]) -> str:
        if label not in assigned:
            numeric = "".join(character for character in label if character.isdigit())
            assigned[label] = f"SPEAKER_{int(numeric):02d}" if numeric else f"SPEAKER_{len(assigned):02d}"
        return assigned[label]

    def diarize(self, media_asset: MediaAsset, audio_path: Path) -> DiarizationResult:
        started = time.monotonic()
        pipeline = self._load_pipeline()
        kwargs = {}
        if self.min_speakers is not None:
            kwargs["min_speakers"] = self.min_speakers
        if self.max_speakers is not None:
            kwargs["max_speakers"] = self.max_speakers
        try:
            annotation = pipeline(str(audio_path), **kwargs)
            raw_segments: list[DiarizationSegmentResult] = []
            assigned: dict[str, str] = {}
            for turn, _, label in annotation.itertracks(yield_label=True):
                raw_segments.append(
                    DiarizationSegmentResult(
                        speaker_id=self._anonymous_id(str(label), assigned),
                        start_time=float(turn.start),
                        end_time=float(turn.end),
                        confidence=None,
                    )
                )
        except Exception as exc:
            raise DiarizationProviderError("Diarization model failed") from exc
        return DiarizationResult(
            segments=raw_segments,
            provider=self.name,
            model=self.model_name,
            device=self.actual_device,
            processing_duration=time.monotonic() - started,
        )


def create_diarization_provider() -> DiarizationProvider:
    if settings.diarization_provider.lower() in {"pyannote", "pyannote.audio"}:
        return PyannoteDiarizationProvider()
    raise DiarizationProviderError(f"Unsupported diarization provider: {settings.diarization_provider}")


def validate_diarization_result(result: DiarizationResult) -> None:
    for segment in result.segments:
        if not segment.speaker_id:
            raise DiarizationProviderError("Diarization returned an empty speaker ID")
        if not math.isfinite(segment.start_time) or not math.isfinite(segment.end_time):
            raise DiarizationProviderError("Diarization returned invalid timestamps")
        if segment.start_time < 0 or segment.end_time <= segment.start_time:
            raise DiarizationProviderError("Diarization returned invalid timestamps")
        if segment.confidence is not None and not 0 <= segment.confidence <= 1:
            raise DiarizationProviderError("Diarization returned invalid confidence")
