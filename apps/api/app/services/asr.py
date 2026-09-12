from __future__ import annotations

import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models.entities import MediaAsset
from app.settings import settings


class ASRProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ASRSegmentResult:
    start_time: float
    end_time: float
    text: str
    language: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class TranscriptResult:
    segments: list[ASRSegmentResult]
    text: str
    provider: str
    model: str
    device: str
    language: str | None
    processing_duration: float


class ASRProvider(ABC):
    name: str

    @abstractmethod
    def transcribe(self, media_asset: MediaAsset, audio_path: Path) -> TranscriptResult:
        raise NotImplementedError


class FasterWhisperProvider(ASRProvider):
    name = "faster-whisper"

    def __init__(
        self,
        model: str = settings.asr_model,
        device: str = settings.asr_device,
        compute_type: str = settings.asr_compute_type,
        language: str | None = settings.asr_language,
        beam_size: int = settings.asr_beam_size,
        vad_filter: bool = settings.asr_vad_filter,
        timeout_seconds: int = settings.asr_timeout_seconds,
    ) -> None:
        self.model_name = model
        self.requested_device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.timeout_seconds = timeout_seconds
        self.actual_device = device

    def _run_transcription(self, audio_path: Path) -> tuple[list[ASRSegmentResult], str | None, str]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ASRProviderError(
                "faster-whisper is not installed; install apps/api/requirements.txt"
            ) from exc

        try:
            model = WhisperModel(
                self.model_name,
                device=self.requested_device,
                compute_type=self.compute_type,
            )
            self.actual_device = self.requested_device
        except Exception as exc:
            if self.requested_device == "cpu":
                raise ASRProviderError("ASR model could not be initialized on CPU") from exc
            try:
                model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
                self.actual_device = "cpu"
            except Exception as fallback_exc:
                raise ASRProviderError("ASR model could not be initialized") from fallback_exc

        try:
            segments, info = model.transcribe(
                str(audio_path),
                language=self.language,
                beam_size=self.beam_size,
                vad_filter=self.vad_filter,
            )
            results: list[ASRSegmentResult] = []
            for segment in segments:
                text = str(getattr(segment, "text", ""))
                no_speech_probability = getattr(segment, "no_speech_prob", None)
                confidence = (
                    max(0.0, min(1.0, 1.0 - float(no_speech_probability)))
                    if no_speech_probability is not None
                    else None
                )
                results.append(
                    ASRSegmentResult(
                        start_time=float(segment.start),
                        end_time=float(segment.end),
                        text=text,
                        language=getattr(info, "language", None),
                        confidence=confidence,
                    )
                )
            return results, getattr(info, "language", None), self.actual_device
        except Exception as exc:
            raise ASRProviderError("ASR model failed during transcription") from exc

    def transcribe(self, media_asset: MediaAsset, audio_path: Path) -> TranscriptResult:
        started = time.monotonic()
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._run_transcription, audio_path)
        try:
            segments, language, device = future.result(timeout=self.timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise ASRProviderError("ASR provider timed out") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if not segments:
            raise ASRProviderError("ASR returned an empty transcript")
        return TranscriptResult(
            segments=segments,
            text="".join(segment.text for segment in segments),
            provider=self.name,
            model=self.model_name,
            device=device,
            language=language,
            processing_duration=time.monotonic() - started,
        )


def create_asr_provider() -> ASRProvider:
    if settings.asr_provider.lower() in {"faster-whisper", "whisper", "local"}:
        return FasterWhisperProvider()
    raise ASRProviderError(f"Unsupported ASR provider: {settings.asr_provider}")
