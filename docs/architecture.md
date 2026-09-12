# Architecture

The repository is a modular monolith with independently deployable web and API applications. Docker Compose provides PostgreSQL with pgvector and Redis, while the API and worker share the same Python package boundary.

Phase 7 adds an asynchronous meeting intelligence boundary after evidence-backed event extraction:

- web client -> REST API
- API -> background worker
- API/worker -> PostgreSQL and Redis
- upload API -> media ingestion service
- media ingestion service -> `StorageProvider`
- `LocalStorageProvider` -> local development filesystem
- `S3StorageProvider` -> configurable object storage
- media ingestion service -> ffprobe inspection
- validated `MediaAsset` -> future ASR/media consumers
- Redis transcription queue -> API worker
- `ASRProvider` -> replaceable local Whisper-compatible implementation
- `Transcript` and ordered `TranscriptSegment` rows -> future diarization consumers
- shared Redis job list -> transcription and diarization worker jobs
- `DiarizationProvider` -> replaceable local pyannote-compatible implementation
- raw `SpeakerSegment` rows -> overlap-preserving evidence
- overlap alignment -> `TranscriptSegment.speaker_id`
- shared Redis job list -> event extraction worker jobs
- `EventExtractionProvider` -> replaceable local/OpenAI-compatible structured-output provider
- `Event` -> `EventEvidence` -> canonical `TranscriptSegment`
- `MeetingIntelligenceRun` -> `MeetingIntelligence` derived presentation sections

Uploads are streamed to a temporary file in chunks, hashed with SHA-256, inspected with ffprobe, stored through the provider abstraction, and then persisted as a `MediaAsset`. Transcription and diarization jobs materialize the asset and extract audio from video through FFmpeg. Diarization persists anonymous speaker IDs and raw overlapping intervals, then assigns each transcript segment to the strongest temporal overlap without deleting the raw intervals. Event extraction consumes only those canonical transcript segments and persists derived events with foreign-key evidence links. The API never exposes local filesystem paths.

Phase 7 synthesizes Phase 6 events but does not resolve temporal conflicts or contradictions. LLM output is derived data; event references are validated against the same meeting and canonical transcript/evidence data remains unchanged.
