from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.entities import (
    ActionStatus,
    DecisionStatus,
    EventType,
    MeetingStatus,
    SourceType,
    DiarizationStatus,
    EventExtractionStatus,
    MeetingIntelligenceStatus,
    TranscriptionStatus,
)


class SchemaBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MeetingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    organization_id: str | None = None
    project_id: str | None = None
    created_by: str | None = None
    source_type: SourceType
    language: str | None = Field(default=None, max_length=32)
    primary_language: str | None = Field(default=None, max_length=32)


class MeetingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    status: MeetingStatus | None = None
    processing_status: str | None = None
    processing_progress: float | None = Field(default=None, ge=0, le=100)
    summary: str | None = None


class MeetingRead(SchemaBase):
    id: str
    title: str
    description: str | None
    organization_id: str | None
    project_id: str | None
    created_by: str | None
    started_at: datetime | None
    ended_at: datetime | None
    status: MeetingStatus
    source_type: SourceType
    language: str | None
    primary_language: str | None
    processing_status: str | None
    processing_progress: float
    summary: str | None
    created_at: datetime
    updated_at: datetime


class TranscriptSegmentRead(SchemaBase):
    id: str
    transcript_id: str
    meeting_id: str
    media_asset_id: str | None
    speaker_id: str | None
    segment_index: int
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    text: str
    language: str | None
    confidence: float | None = Field(default=None, ge=0, le=1)
    word_timestamps: list | None
    sequence: int


class TranscriptRead(SchemaBase):
    id: str
    meeting_id: str
    media_asset_id: str | None
    text: str | None
    detected_language: str | None
    provider: str | None
    provider_model: str | None
    provider_device: str | None
    processing_duration: float | None
    configuration_key: str | None
    status: TranscriptionStatus
    error: str | None
    created_at: datetime
    segments: list[TranscriptSegmentRead]


class TranscriptionJobResponse(BaseModel):
    job_id: str
    meeting_id: str
    status: TranscriptionStatus


class JobStatusRead(BaseModel):
    job_id: str
    meeting_id: str
    status: str
    error: str | None = None


class DiarizationJobResponse(BaseModel):
    job_id: str
    meeting_id: str
    status: DiarizationStatus


class DiarizationStatusRead(BaseModel):
    status: DiarizationStatus
    error: str | None = None


class EventEvidenceRead(BaseModel):
    id: str
    transcript_segment_id: str
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    text: str
    speaker_id: str | None
    speaker_label: str | None


class MeetingEventRead(BaseModel):
    id: str
    meeting_id: str
    extraction_run_id: str | None
    event_type: EventType
    title: str | None
    subject: str | None
    value: str | None
    speaker_id: str | None
    speaker_label: str | None
    speaker_display_name: str | None
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[EventEvidenceRead]


class EventExtractionJobResponse(BaseModel):
    job_id: str
    meeting_id: str
    status: EventExtractionStatus


class EventExtractionStatusRead(BaseModel):
    status: EventExtractionStatus
    provider: str | None
    model: str | None
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    event_count: int


class IntelligenceGenerateResponse(BaseModel):
    job_id: str
    meeting_id: str
    status: MeetingIntelligenceStatus


class MeetingIntelligenceRead(BaseModel):
    id: str
    meeting_id: str
    generation_run_id: str
    provider: str
    model: str | None
    configuration_key: str
    status: MeetingIntelligenceStatus
    summary: str
    sections: dict
    generated_at: datetime | None
    created_at: datetime


class MeetingIntelligenceStatusRead(BaseModel):
    status: MeetingIntelligenceStatus
    provider: str | None
    model: str | None
    generated_at: datetime | None
    error: str | None
    event_count: int


class SpeakerRead(SchemaBase):
    id: str
    meeting_id: str
    media_asset_id: str | None
    label: str
    display_name: str | None
    provider: str | None
    provider_model: str | None
    segments_count: int
    speaking_duration_seconds: float


class SpeakerSegmentRead(SchemaBase):
    id: str
    meeting_id: str
    media_asset_id: str | None
    speaker_id: str
    speaker_label: str
    speaker_display_name: str | None
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    provider: str | None
    provider_model: str | None


class SpeakerMappingUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)


class EventRead(SchemaBase):
    id: str
    meeting_id: str
    speaker_id: str | None
    topic_id: str | None
    event_type: EventType
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    language: str | None
    original_text: str
    translated_text: str | None
    subject: str | None
    predicate: str | None
    object_value: str | None
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    confidence_label: str | None
    schema_version: int


class DecisionCreate(BaseModel):
    meeting_id: str
    statement: str = Field(min_length=1)
    topic: str | None = None
    decision_maker: str | None = None
    timestamp: float = Field(ge=0)
    status: DecisionStatus = DecisionStatus.UNKNOWN
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    event_id: str | None = None


class DecisionRead(SchemaBase):
    id: str
    meeting_id: str
    event_id: str | None
    statement: str
    topic: str | None
    decision_maker: str | None
    timestamp: float = Field(ge=0)
    status: DecisionStatus
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    created_at: datetime
    updated_at: datetime


class ActionItemCreate(BaseModel):
    meeting_id: str
    task: str = Field(min_length=1)
    owner_id: str | None = None
    deadline: str | None = None
    priority: str | None = None
    status: ActionStatus = ActionStatus.UNKNOWN
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    source_event_id: str | None = None


class ActionItemRead(SchemaBase):
    id: str
    meeting_id: str
    source_event_id: str | None
    owner_id: str | None
    task: str
    deadline: str | None
    priority: str | None
    status: ActionStatus
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    created_at: datetime
    updated_at: datetime


class MediaAssetRead(SchemaBase):
    id: str
    meeting_id: str
    filename: str
    original_filename: str
    mime_type: str
    size_bytes: int
    duration_seconds: float | None
    codec: str | None
    sample_rate: int | None
    channels: int | None
    resolution: str | None
    width: int | None
    height: int | None
    fps: float | None
    checksum: str
    storage_key: str
    created_at: datetime
