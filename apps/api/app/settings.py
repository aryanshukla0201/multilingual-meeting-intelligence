from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://meeting:meeting@localhost:5432/meeting_intelligence"
    redis_url: str = "redis://localhost:6379/0"
    storage_provider: str = "local"
    storage_path: str = "./data"
    storage_bucket: str | None = None
    storage_region: str | None = None
    storage_endpoint_url: str | None = None
    storage_access_key_id: str | None = None
    storage_secret_access_key: str | None = None
    max_media_size_bytes: int = 524_288_000
    ffprobe_path: str = "ffprobe"
    ffmpeg_path: str = "ffmpeg"
    upload_chunk_size: int = 1_048_576
    asr_provider: str = "faster-whisper"
    asr_model: str = "base"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    asr_language: str | None = None
    asr_beam_size: int = 5
    asr_vad_filter: bool = True
    asr_timeout_seconds: int = 1800
    transcription_queue: str = "meeting-intelligence:transcription"
    diarization_provider: str = "pyannote"
    diarization_model: str = "pyannote/speaker-diarization-3.1"
    diarization_device: str = "cpu"
    diarization_min_speakers: int | None = None
    diarization_max_speakers: int | None = None
    diarization_auth_token: str | None = None
    event_extraction_provider: str = "openai-compatible"
    event_extraction_model: str = ""
    event_extraction_temperature: float = 0.0
    event_extraction_timeout: int = 120
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    meeting_intelligence_provider: str = "openai-compatible"
    meeting_intelligence_model: str = ""
    meeting_intelligence_temperature: float = 0.0
    meeting_intelligence_timeout: int = 120
    database_echo: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
