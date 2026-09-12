from __future__ import annotations

import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.models.entities import MediaAsset
from app.services.media_inspection import MediaValidationError, inspect_media
from app.services.storage import StorageError, StorageProvider
from app.settings import settings


class AudioPreparationError(RuntimeError):
    pass


@contextmanager
def audio_path_for_asset(
    asset: MediaAsset,
    storage: StorageProvider,
    ffmpeg_path: str,
) -> Iterator[Path]:
    suffix = Path(asset.filename).suffix.lower() or ".media"
    with tempfile.NamedTemporaryFile(prefix="meeting-asr-source-", suffix=suffix, delete=False) as source_file:
        source_path = Path(source_file.name)
    source_path.unlink(missing_ok=True)
    output_path: Path | None = None
    try:
        try:
            storage.materialize(asset.storage_key, source_path)
        except StorageError as exc:
            raise AudioPreparationError("Media storage object is unavailable") from exc
        if source_path.stat().st_size == 0:
            raise AudioPreparationError("Media asset is empty")
        is_video = asset.mime_type.startswith("video/") or suffix in {".mp4", ".mov", ".webm"}
        if not is_video:
            try:
                inspect_media(source_path, suffix, settings.ffprobe_path)
            except MediaValidationError as exc:
                raise AudioPreparationError("Media asset is not readable audio") from exc
            yield source_path
            return

        with tempfile.NamedTemporaryFile(prefix="meeting-asr-audio-", suffix=".wav", delete=False) as output_file:
            output_path = Path(output_file.name)
        output_path.unlink(missing_ok=True)
        try:
            completed = subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(source_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise AudioPreparationError("Audio extraction is unavailable") from exc
        if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            raise AudioPreparationError("Audio extraction failed")
        yield output_path
    finally:
        source_path.unlink(missing_ok=True)
        if output_path is not None:
            output_path.unlink(missing_ok=True)
