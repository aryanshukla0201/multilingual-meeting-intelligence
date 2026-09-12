# AI Pipeline

## Phase 3: ASR

Phase 3 adds only automatic speech recognition. It does not perform speaker diarization, translation, language detection beyond the language returned by ASR, event extraction, summarization, or meeting intelligence.

The flow is:

```text
MediaAsset
  -> StorageProvider.materialize()
  -> FFmpeg audio extraction for video
  -> ASRProvider
  -> TranscriptResult
  -> Transcript + ordered TranscriptSegment rows
```

`ASRProvider` is defined in `apps/api/app/services/asr.py`. The production implementation is `FasterWhisperProvider`, which runs locally through `faster-whisper`. The transcript service does not depend directly on the Whisper library; tests and future cloud providers can implement the same interface.

## Local configuration

Set these variables in `.env`:

```text
ASR_PROVIDER=faster-whisper
ASR_MODEL=base
ASR_DEVICE=cpu
ASR_COMPUTE_TYPE=int8
ASR_LANGUAGE=
ASR_BEAM_SIZE=5
ASR_VAD_FILTER=true
ASR_TIMEOUT_SECONDS=1800
```

The Python package is installed from `apps/api/requirements.txt`. The model itself is loaded lazily by the worker and may be downloaded by `faster-whisper` on first use. Automated tests inject a deterministic fake provider and never download a model.

For GPU use, set `ASR_DEVICE=cuda` and a compatible `ASR_COMPUTE_TYPE`. If GPU initialization fails, the provider falls back to CPU with `int8`; provider failures are stored as `TRANSCRIPTION_FAILED` and never produce fabricated text.

## Asynchronous processing

`POST /api/meetings/{id}/transcribe` places a job on the configured Redis list. The API returns immediately. The `worker` service consumes the job, materializes media, extracts audio when necessary, runs ASR, and persists the transcript. Job status is available through `GET /api/jobs/{job_id}`.

Transcript rows record provider, model, device, detected language, processing duration, configuration key, and status. Segment rows preserve provider text and timestamps exactly, with sequence ordering and optional confidence.

Successful transcription is idempotent for the same media asset and ASR configuration. Failed runs preserve an explicit failure state and can be retried without duplicating successful transcript rows.

## Phase 4: Speaker diarization

`DiarizationProvider` is defined in `apps/api/app/services/diarization.py`. The production implementation is `PyannoteDiarizationProvider`, which loads a pyannote-compatible pipeline locally when the optional `requirements-diarization.txt` dependencies are installed.

Configure:

```text
DIARIZATION_PROVIDER=pyannote
DIARIZATION_MODEL=pyannote/speaker-diarization-3.1
DIARIZATION_DEVICE=cpu
DIARIZATION_MIN_SPEAKERS=
DIARIZATION_MAX_SPEAKERS=
DIARIZATION_AUTH_TOKEN=
```

Install the optional runtime with `python -m pip install -r apps/api/requirements-diarization.txt`. Authentication is read only from `DIARIZATION_AUTH_TOKEN`; tokens are never stored in source or logs.

Diarization jobs reuse the transcription Redis queue and worker. Video assets are converted to temporary WAV audio through the existing audio preparation service. Raw intervals are persisted independently, including overlaps. Transcript assignment uses the greatest positive temporal overlap and leaves the transcript segment unassigned when no overlap exists.

Speakers remain anonymous (`SPEAKER_00`, `SPEAKER_01`, and so on). The mapping API changes only `display_name`; it never changes the anonymous label or raw diarization data. Re-running with the same media asset and provider configuration reuses the completed `DiarizationRun`.

## Phase 6: Event extraction

Phase 6 consumes speaker-aware transcript segments and produces controlled event types: `COMMITMENT`, `DECISION`, `ACTION`, `PROPOSAL`, `QUESTION`, `RISK`, `CLAIM`, `CONSTRAINT`, and `ASSUMPTION`.

`EventExtractionProvider` supports an OpenAI-compatible structured-output endpoint and is replaceable without changing the extraction service. Configure:

```text
EVENT_EXTRACTION_PROVIDER=openai-compatible
EVENT_EXTRACTION_MODEL=
EVENT_EXTRACTION_TEMPERATURE=0
EVENT_EXTRACTION_TIMEOUT=120
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=
```

The versioned prompt is `apps/api/app/prompts/event_extraction_v1.txt`. Provider output passes through Pydantic validation, same-meeting transcript ID validation, speaker derivation, and evidence timestamp derivation before any event is persisted.

Each event is linked through `EventEvidence` to one or more canonical `TranscriptSegment` rows. Multi-segment events use the minimum and maximum evidence timestamps. Invalid or cross-meeting evidence fails the extraction run transactionally. Phase 6 extracts events but does not resolve temporal conflicts or contradictions, determine a latest value, or infer real-world identities.

## Phase 7: Meeting intelligence

Meeting intelligence consumes only completed Phase 6 events and produces an executive summary plus structured decisions, commitments, actions, risks, questions, proposals, claims, constraints, assumptions, highlights, and open items.

Configure the replaceable provider with:

```text
MEETING_INTELLIGENCE_PROVIDER=openai-compatible
MEETING_INTELLIGENCE_MODEL=
MEETING_INTELLIGENCE_TEMPERATURE=0
MEETING_INTELLIGENCE_TIMEOUT=120
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=
```

The versioned prompt is `apps/api/app/prompts/meeting_intelligence_v1.txt`. Every structured item references existing Phase 6 event IDs. The service rejects nonexistent or cross-meeting references before persistence.

Phase 7 synthesizes Phase 6 events but does not resolve temporal conflicts or contradictions. A Friday commitment and Monday proposal remain separate items; neither is marked latest, obsolete, or superseding.
