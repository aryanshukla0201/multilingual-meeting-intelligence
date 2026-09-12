# Multilingual Meeting Intelligence

Phase 7 provides validated media ingestion, asynchronous local ASR, anonymous speaker diarization, transcript-speaker alignment, evidence-backed event extraction, and meeting-level intelligence synthesis. Later phases will add multilingual processing, contradiction reasoning, and memory features behind replaceable interfaces.

## Repository layout

- `apps/api`: FastAPI service and worker entry points
- `apps/web`: Next.js application shell
- `packages`: shared contracts and reusable packages
- `infrastructure`: Docker/PostgreSQL support
- `data`: local development storage mounts
- `docs`: architecture and operational notes

## Quick start

1. Copy `.env.example` to `.env` and adjust provider settings when integrations are added.
2. Start infrastructure and application shells:

   ```powershell
   docker compose up --build
   ```

3. Open `http://localhost:3000` for the web shell and `http://localhost:8000/docs` for FastAPI OpenAPI docs.

Phase 7 exposes media ingestion, transcription, diarization, speaker mapping, timestamped transcript, event extraction, and meeting intelligence endpoints. It intentionally does not resolve contradictions, perform temporal reasoning, translate, search, or build organizational memory.

## Local checks

```powershell
python -m compileall apps/api
python -m pip install -r apps/api/requirements-dev.txt
python -m pytest apps/api/tests
alembic upgrade head
npm --prefix apps/web install
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint
docker compose config
```

The leading space before `docker` is optional and can be omitted.

To apply the schema from the host, start PostgreSQL and use `localhost`:

```powershell
docker compose up -d postgres
$env:DATABASE_URL = "postgresql+psycopg://meeting:meeting@localhost:5432/meeting_intelligence"
alembic upgrade head
```
