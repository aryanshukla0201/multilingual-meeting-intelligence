from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator
from pgvector.sqlalchemy import Vector

from app.db.base import Base


def new_id() -> str:
    return str(uuid4())


class EmbeddingVector(TypeDecorator[list]):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(1536))
        return dialect.type_descriptor(JSON())


class MeetingStatus(StrEnum):
    UPLOADED = "UPLOADED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    TRANSCRIBING = "TRANSCRIBING"
    DIARIZING = "DIARIZING"
    LANGUAGE_ANALYSIS = "LANGUAGE_ANALYSIS"
    TRANSLATING = "TRANSLATING"
    EVENT_EXTRACTION = "EVENT_EXTRACTION"
    INTELLIGENCE_ANALYSIS = "INTELLIGENCE_ANALYSIS"
    INDEXING = "INDEXING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SourceType(StrEnum):
    AUDIO = "AUDIO"
    VIDEO = "VIDEO"


class TranscriptionStatus(StrEnum):
    TRANSCRIPTION_QUEUED = "TRANSCRIPTION_QUEUED"
    TRANSCRIBING = "TRANSCRIBING"
    TRANSCRIBED = "TRANSCRIBED"
    TRANSCRIPTION_FAILED = "TRANSCRIPTION_FAILED"


class DiarizationStatus(StrEnum):
    DIARIZATION_NOT_STARTED = "DIARIZATION_NOT_STARTED"
    DIARIZATION_QUEUED = "DIARIZATION_QUEUED"
    DIARIZATION_PROCESSING = "DIARIZATION_PROCESSING"
    DIARIZATION_COMPLETED = "DIARIZATION_COMPLETED"
    DIARIZATION_FAILED = "DIARIZATION_FAILED"


class DecisionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    UNDER_DISCUSSION = "UNDER_DISCUSSION"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    REVERSED = "REVERSED"
    UNKNOWN = "UNKNOWN"


class ActionStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    OVERDUE = "OVERDUE"
    UNKNOWN = "UNKNOWN"


class EventType(StrEnum):
    FACT = "FACT"
    CLAIM = "CLAIM"
    OPINION = "OPINION"
    DECISION = "DECISION"
    COMMITMENT = "COMMITMENT"
    ACTION = "ACTION"
    DEADLINE = "DEADLINE"
    RISK = "RISK"
    BLOCKER = "BLOCKER"
    QUESTION = "QUESTION"
    DISAGREEMENT = "DISAGREEMENT"
    PROPOSAL = "PROPOSAL"
    OBJECTION = "OBJECTION"
    ASSUMPTION = "ASSUMPTION"
    CONSTRAINT = "CONSTRAINT"


class EvidenceType(StrEnum):
    TRANSCRIPT = "TRANSCRIPT"
    AUDIO = "AUDIO"
    VIDEO = "VIDEO"
    SCREEN = "SCREEN"
    OCR = "OCR"
    DOCUMENT = "DOCUMENT"
    PREVIOUS_MEETING = "PREVIOUS_MEETING"
    DATABASE_RECORD = "DATABASE_RECORD"


class ContradictionType(StrEnum):
    EXPLICIT = "EXPLICIT"
    COMMITMENT_CHANGED = "COMMITMENT_CHANGED"
    DECISION_REVERSED = "DECISION_REVERSED"
    DEADLINE_CHANGED = "DEADLINE_CHANGED"
    OWNER_CHANGED = "OWNER_CHANGED"
    SCOPE_CHANGED = "SCOPE_CHANGED"
    STATUS_CHANGED = "STATUS_CHANGED"


class BaseEntity(Base):
    __abstract__ = True

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(BaseEntity):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Organization(BaseEntity):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), unique=True)


class Person(BaseEntity):
    __tablename__ = "people"

    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), index=True)


class Project(BaseEntity):
    __tablename__ = "projects"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)


class Meeting(BaseEntity):
    __tablename__ = "meetings"

    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[MeetingStatus] = mapped_column(String(32), default=MeetingStatus.UPLOADED, index=True)
    source_type: Mapped[SourceType] = mapped_column(String(16))
    language: Mapped[str | None] = mapped_column(String(32))
    primary_language: Mapped[str | None] = mapped_column(String(32))
    processing_status: Mapped[str | None] = mapped_column(String(64))
    processing_progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)


class Participant(BaseEntity):
    __tablename__ = "participants"
    __table_args__ = (Index("ix_participants_meeting_person", "meeting_id", "person_id", unique=True),)

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[str | None] = mapped_column(ForeignKey("people.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    speaker_label: Mapped[str | None] = mapped_column(String(100))


class MediaAsset(BaseEntity):
    __tablename__ = "media_assets"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    original_filename: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    codec: Mapped[str | None] = mapped_column(String(64))
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[int | None] = mapped_column(Integer)
    resolution: Mapped[str | None] = mapped_column(String(32))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    checksum: Mapped[str] = mapped_column(String(128), index=True)
    storage_key: Mapped[str] = mapped_column(String(1000), unique=True)


class Transcript(BaseEntity):
    __tablename__ = "transcripts"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), unique=True, index=True)
    media_asset_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"), index=True)
    text: Mapped[str | None] = mapped_column(Text)
    detected_language: Mapped[str | None] = mapped_column(String(32))
    provider: Mapped[str | None] = mapped_column(String(100))
    provider_model: Mapped[str | None] = mapped_column(String(200))
    provider_device: Mapped[str | None] = mapped_column(String(64))
    processing_duration: Mapped[float | None] = mapped_column(Float)
    configuration_key: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[TranscriptionStatus] = mapped_column(
        String(32), default=TranscriptionStatus.TRANSCRIPTION_QUEUED, index=True
    )
    error: Mapped[str | None] = mapped_column(Text)


class Speaker(BaseEntity):
    __tablename__ = "speakers"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    media_asset_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"), index=True)
    label: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str | None] = mapped_column(String(200))
    person_id: Mapped[str | None] = mapped_column(ForeignKey("people.id"), index=True)
    confidence: Mapped[float | None] = mapped_column(Float)
    provider: Mapped[str | None] = mapped_column(String(100))
    provider_model: Mapped[str | None] = mapped_column(String(200))
    configuration_key: Mapped[str | None] = mapped_column(String(128), index=True)


class TranscriptSegment(BaseEntity):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        Index("ix_transcript_segments_meeting_time", "meeting_id", "start_time"),
        Index("ix_transcript_segments_media_time", "media_asset_id", "start_time"),
        Index("ix_transcript_segments_meeting_sequence", "meeting_id", "segment_index"),
    )

    transcript_id: Mapped[str] = mapped_column(ForeignKey("transcripts.id", ondelete="CASCADE"), index=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    media_asset_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"), index=True)
    speaker_id: Mapped[str | None] = mapped_column(ForeignKey("speakers.id"), index=True)
    segment_index: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Float)
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    word_timestamps: Mapped[list | None] = mapped_column(JSON)


class SpeakerSegment(BaseEntity):
    __tablename__ = "speaker_segments"
    __table_args__ = (
        Index("ix_speaker_segments_meeting_time", "meeting_id", "start_time"),
        Index("ix_speaker_segments_media_time", "media_asset_id", "start_time"),
    )

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    media_asset_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"), index=True)
    speaker_id: Mapped[str] = mapped_column(ForeignKey("speakers.id"), index=True)
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    provider: Mapped[str | None] = mapped_column(String(100))
    provider_model: Mapped[str | None] = mapped_column(String(200))


class DiarizationRun(BaseEntity):
    __tablename__ = "diarization_runs"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    media_asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(100))
    provider_model: Mapped[str | None] = mapped_column(String(200))
    provider_device: Mapped[str | None] = mapped_column(String(64))
    configuration_key: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[DiarizationStatus] = mapped_column(
        String(40), default=DiarizationStatus.DIARIZATION_NOT_STARTED, index=True
    )
    error: Mapped[str | None] = mapped_column(Text)


class EventExtractionStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeetingIntelligenceStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EventExtractionRun(BaseEntity):
    __tablename__ = "event_extraction_runs"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(100))
    provider_model: Mapped[str | None] = mapped_column(String(200))
    configuration_key: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[EventExtractionStatus] = mapped_column(
        String(32), default=EventExtractionStatus.NOT_STARTED, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class LanguageSegment(BaseEntity):
    __tablename__ = "language_segments"

    transcript_segment_id: Mapped[str] = mapped_column(ForeignKey("transcript_segments.id", ondelete="CASCADE"), index=True)
    language: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Float)
    is_code_switch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Translation(BaseEntity):
    __tablename__ = "translations"

    transcript_segment_id: Mapped[str] = mapped_column(ForeignKey("transcript_segments.id", ondelete="CASCADE"), index=True)
    original_text: Mapped[str] = mapped_column(Text)
    original_language: Mapped[str] = mapped_column(String(32))
    translated_text: Mapped[str] = mapped_column(Text)
    target_language: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str | None] = mapped_column(String(100))


class Topic(BaseEntity):
    __tablename__ = "topics"
    __table_args__ = (Index("ix_topics_meeting_time", "meeting_id", "start_time"),)

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    summary: Mapped[str | None] = mapped_column(Text)


class Event(BaseEntity):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_meeting_time_type", "meeting_id", "start_time", "event_type"),
        Index("ix_events_extraction_run", "extraction_run_id"),
        CheckConstraint(
            "event_type IN ('FACT', 'CLAIM', 'OPINION', 'DECISION', 'COMMITMENT', 'ACTION', 'DEADLINE', 'RISK', 'BLOCKER', 'QUESTION', 'DISAGREEMENT', 'PROPOSAL', 'OBJECTION', 'ASSUMPTION', 'CONSTRAINT')",
            name="ck_events_event_type",
        ),
    )

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    speaker_id: Mapped[str | None] = mapped_column(ForeignKey("speakers.id"), index=True)
    extraction_run_id: Mapped[str | None] = mapped_column(ForeignKey("event_extraction_runs.id"), index=True)
    topic_id: Mapped[str | None] = mapped_column(ForeignKey("topics.id"), index=True)
    event_type: Mapped[EventType] = mapped_column(String(32), index=True)
    title: Mapped[str | None] = mapped_column(String(300))
    value: Mapped[str | None] = mapped_column(Text)
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    language: Mapped[str | None] = mapped_column(String(32))
    original_text: Mapped[str] = mapped_column(Text)
    translated_text: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(300))
    predicate: Mapped[str | None] = mapped_column(String(300))
    object_value: Mapped[str | None] = mapped_column(String(1000))
    confidence_score: Mapped[float | None] = mapped_column(Float)
    confidence_label: Mapped[str | None] = mapped_column(String(16))
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class EventEvidence(BaseEntity):
    __tablename__ = "event_evidence"
    __table_args__ = (
        Index("ix_event_evidence_event", "event_id"),
        Index("ix_event_evidence_transcript_segment", "transcript_segment_id"),
    )

    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    transcript_segment_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE"), index=True
    )
    evidence_start: Mapped[float] = mapped_column(Float)
    evidence_end: Mapped[float] = mapped_column(Float)
    relevance: Mapped[float | None] = mapped_column(Float)


class MeetingIntelligenceRun(BaseEntity):
    __tablename__ = "meeting_intelligence_runs"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(100))
    provider_model: Mapped[str | None] = mapped_column(String(200))
    configuration_key: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[MeetingIntelligenceStatus] = mapped_column(
        String(32), default=MeetingIntelligenceStatus.NOT_STARTED, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class MeetingIntelligence(BaseEntity):
    __tablename__ = "meeting_intelligence"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    generation_run_id: Mapped[str] = mapped_column(
        ForeignKey("meeting_intelligence_runs.id", ondelete="CASCADE"), unique=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(100))
    provider_model: Mapped[str | None] = mapped_column(String(200))
    configuration_key: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[MeetingIntelligenceStatus] = mapped_column(
        String(32), default=MeetingIntelligenceStatus.COMPLETED, index=True
    )
    summary: Mapped[str] = mapped_column(Text)
    sections: Mapped[dict] = mapped_column(JSON)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Decision(BaseEntity):
    __tablename__ = "decisions"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"), index=True)
    statement: Mapped[str] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(300))
    decision_maker: Mapped[str | None] = mapped_column(ForeignKey("people.id"), index=True)
    timestamp: Mapped[float] = mapped_column(Float)
    status: Mapped[DecisionStatus] = mapped_column(String(32), default=DecisionStatus.UNKNOWN)
    confidence_score: Mapped[float | None] = mapped_column(Float)


class Commitment(BaseEntity):
    __tablename__ = "commitments"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"), index=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("people.id"), index=True)
    task: Mapped[str] = mapped_column(Text)
    deadline: Mapped[str | None] = mapped_column(String(200))
    scope: Mapped[str | None] = mapped_column(Text)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="OPEN")


class ActionItem(BaseEntity):
    __tablename__ = "action_items"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    source_event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"), index=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("people.id"), index=True)
    task: Mapped[str] = mapped_column(Text)
    deadline: Mapped[str | None] = mapped_column(String(200))
    priority: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[ActionStatus] = mapped_column(String(32), default=ActionStatus.UNKNOWN, index=True)
    confidence_score: Mapped[float | None] = mapped_column(Float)


class Risk(BaseEntity):
    __tablename__ = "risks"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"), index=True)
    risk: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64))
    severity: Mapped[str | None] = mapped_column(String(32))
    probability: Mapped[str | None] = mapped_column(String(32))
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("people.id"), index=True)
    mitigation: Mapped[str | None] = mapped_column(Text)
    confidence_score: Mapped[float | None] = mapped_column(Float)


class Question(BaseEntity):
    __tablename__ = "questions"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    asked_by: Mapped[str | None] = mapped_column(ForeignKey("people.id"), index=True)
    timestamp: Mapped[float] = mapped_column(Float)
    topic: Mapped[str | None] = mapped_column(String(300))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolution_event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"))
    confidence_score: Mapped[float | None] = mapped_column(Float)


class Contradiction(BaseEntity):
    __tablename__ = "contradictions"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    old_event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    new_event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    contradiction_type: Mapped[ContradictionType] = mapped_column(String(32))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="SUPERSEDED")
    confidence_score: Mapped[float | None] = mapped_column(Float)


class Evidence(BaseEntity):
    __tablename__ = "evidence"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[EvidenceType] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    start_time: Mapped[float | None] = mapped_column(Float)
    end_time: Mapped[float | None] = mapped_column(Float)
    speaker_id: Mapped[str | None] = mapped_column(ForeignKey("speakers.id"), index=True)
    excerpt: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class ScreenFrame(BaseEntity):
    __tablename__ = "screen_frames"

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    media_asset_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"), index=True)
    timestamp: Mapped[float] = mapped_column(Float, index=True)
    storage_key: Mapped[str] = mapped_column(String(1000))
    checksum: Mapped[str | None] = mapped_column(String(128))


class VisualObservation(BaseEntity):
    __tablename__ = "visual_observations"

    frame_id: Mapped[str] = mapped_column(ForeignKey("screen_frames.id", ondelete="CASCADE"), index=True)
    screen_type: Mapped[str | None] = mapped_column(String(100))
    analysis: Mapped[str | None] = mapped_column(Text)
    entities: Mapped[list | None] = mapped_column(JSON)
    relationships: Mapped[list | None] = mapped_column(JSON)
    confidence_score: Mapped[float | None] = mapped_column(Float)


class Embedding(BaseEntity):
    __tablename__ = "embeddings"
    __table_args__ = (Index("ix_embeddings_source", "source_type", "source_id"),)

    meeting_id: Mapped[str | None] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[list] = mapped_column(EmbeddingVector)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    source_timestamp: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class MeetingRelation(BaseEntity):
    __tablename__ = "meeting_relations"

    from_meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    to_meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    relation_type: Mapped[str] = mapped_column(String(64), index=True)
    confidence_score: Mapped[float | None] = mapped_column(Float)


class DecisionHistory(BaseEntity):
    __tablename__ = "decision_history"

    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id", ondelete="CASCADE"), index=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"), index=True)
    value: Mapped[str] = mapped_column(Text)
    status: Mapped[DecisionStatus] = mapped_column(String(32), default=DecisionStatus.UNKNOWN)
    timestamp: Mapped[float] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
