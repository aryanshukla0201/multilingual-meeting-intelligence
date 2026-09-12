from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


ALLOWED_MEDIA: dict[str, tuple[str, ...]] = {
    ".mp3": ("audio/mpeg",),
    ".wav": ("audio/wav", "audio/x-wav", "audio/wave"),
    ".m4a": ("audio/mp4", "audio/x-m4a"),
    ".flac": ("audio/flac", "audio/x-flac"),
    ".mp4": ("video/mp4",),
    ".mov": ("video/quicktime",),
    ".webm": ("video/webm",),
}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}


class MediaValidationError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class MediaMetadata:
    duration_seconds: float | None
    codec: str | None
    sample_rate: int | None
    channels: int | None
    width: int | None
    height: int | None
    fps: float | None


def safe_original_filename(filename: str | None) -> str:
    candidate = Path(filename or "").name.strip()
    if not candidate or candidate in {".", ".."}:
        raise MediaValidationError("INVALID_FILENAME", "A filename is required")
    return candidate


def validate_extension_and_mime(filename: str, mime_type: str | None) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_MEDIA:
        raise MediaValidationError("UNSUPPORTED_EXTENSION", "Unsupported media extension")
    if not mime_type or mime_type.lower() not in ALLOWED_MEDIA[extension]:
        raise MediaValidationError("INVALID_MIME_TYPE", "MIME type does not match the file extension")
    return extension


def _parse_rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return None


def inspect_media(path: Path, extension: str, ffprobe_path: str = "ffprobe") -> MediaMetadata:
    try:
        completed = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,sample_rate,channels,width,height,avg_frame_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise MediaValidationError("MEDIA_INSPECTION_FAILED", "Media inspection is unavailable") from exc
    if completed.returncode != 0:
        raise MediaValidationError("UNREADABLE_MEDIA", "The uploaded media could not be read")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MediaValidationError("UNREADABLE_MEDIA", "The media inspection returned invalid data") from exc

    streams = payload.get("streams", [])
    expected_type = "audio" if extension in AUDIO_EXTENSIONS else "video"
    matching_streams = [stream for stream in streams if stream.get("codec_type") == expected_type]
    if not matching_streams:
        raise MediaValidationError("UNSUPPORTED_MEDIA_FORMAT", "The file has no supported audio or video stream")
    stream = matching_streams[0]
    format_data = payload.get("format", {})

    duration_raw = format_data.get("duration")
    try:
        duration = float(duration_raw) if duration_raw is not None else None
    except (TypeError, ValueError):
        duration = None
    if duration is not None and duration <= 0:
        raise MediaValidationError("UNREADABLE_MEDIA", "The media duration is invalid")

    def integer_value(name: str) -> int | None:
        value = stream.get(name)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return MediaMetadata(
        duration_seconds=duration,
        codec=stream.get("codec_name"),
        sample_rate=integer_value("sample_rate"),
        channels=integer_value("channels"),
        width=integer_value("width"),
        height=integer_value("height"),
        fps=_parse_rate(stream.get("avg_frame_rate")),
    )
