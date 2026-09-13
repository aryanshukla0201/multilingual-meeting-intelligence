from fastapi import FastAPI
from pydantic import BaseModel

from app.api.media import router as media_router
from app.api.transcription import router as transcription_router
from app.api.speakers import router as speakers_router
from app.api.events import router as events_router
from app.api.evidence import router as evidence_router
from app.api.intelligence import router as intelligence_router
from app.settings import settings


class HealthResponse(BaseModel):
    status: str
    environment: str


app = FastAPI(
    title="Multilingual Meeting Intelligence API",
    version="0.1.0",
    description="Phase 2 media ingestion API; downstream meeting intelligence is added in later phases.",
)

app.include_router(media_router)
app.include_router(transcription_router)
app.include_router(speakers_router)
app.include_router(events_router)
app.include_router(evidence_router)
app.include_router(intelligence_router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", environment=settings.app_env)


@app.get("/ready", response_model=HealthResponse, tags=["system"])
def ready() -> HealthResponse:
    return HealthResponse(status="ready", environment=settings.app_env)
