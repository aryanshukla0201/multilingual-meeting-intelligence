# Deployment

For local development, copy `.env.example` to `.env` and run `docker compose up --build`. The compose file exposes PostgreSQL on `5432`, Redis on `6379`, the API on `8000`, and the web shell on `3000`. Local media is stored under the configured `STORAGE_PATH` and is mounted from `data/` in Compose.

Phase 3 requires `ffmpeg` and `ffprobe`; both are installed in the API image. The API accepts `mp3`, `wav`, `m4a`, `flac`, `mp4`, `mov`, and `webm`. Set `STORAGE_PROVIDER=s3` plus the S3 settings in `.env` to use object storage; local development does not require S3 credentials.

The worker consumes transcription jobs from Redis. Configure the local ASR provider with `ASR_PROVIDER`, `ASR_MODEL`, `ASR_DEVICE`, and `ASR_COMPUTE_TYPE`. The model is loaded lazily when a worker processes its first job, so starting the stack does not download a model.

Speaker diarization uses the same worker and Redis queue. Install the optional local pyannote runtime with `python -m pip install -r apps/api/requirements-diarization.txt`, then configure `DIARIZATION_MODEL`, `DIARIZATION_DEVICE`, and `DIARIZATION_AUTH_TOKEN`. The pyannote model may require an accepted model license and a Hugging Face token. Tests do not install or download the diarization model.

Event extraction also uses the same worker and queue. Set `EVENT_EXTRACTION_PROVIDER`, `EVENT_EXTRACTION_MODEL`, and `LLM_BASE_URL`. A local OpenAI-compatible server can be used without sending transcript content to an external service. The API starts without a configured model; the provider is invoked only when an extraction job is processed.

Meeting intelligence uses the same worker and Redis queue after event extraction completes. Set `MEETING_INTELLIGENCE_PROVIDER`, `MEETING_INTELLIGENCE_MODEL`, `MEETING_INTELLIGENCE_TEMPERATURE`, and `MEETING_INTELLIGENCE_TIMEOUT`. The service starts without a configured intelligence model; only a generation request invokes the provider.

Apply migrations from the host with the host database URL:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://meeting:meeting@localhost:5432/meeting_intelligence"
alembic upgrade head
```

Production deployment is intentionally deferred. Before exposing the services publicly, add authentication, secret management, TLS, persistent object storage, and non-default database credentials.
