"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";

type TranscriptSegment = {
  id: string;
  start_time: number;
  end_time: number;
  text: string;
  language: string | null;
  confidence: number | null;
  sequence: number;
  speaker_id: string | null;
};

type Speaker = {
  id: string;
  label: string;
  display_name: string | null;
  segments_count: number;
  speaking_duration_seconds: number;
};

type DiarizationStatus = {
  status: string;
  error: string | null;
};

type EventEvidence = {
  id: string;
  transcript_segment_id: string;
  start_time: number;
  end_time: number;
  text: string;
  speaker_label: string | null;
};

type MeetingEvent = {
  id: string;
  event_type: string;
  title: string | null;
  subject: string | null;
  value: string | null;
  speaker_label: string | null;
  speaker_display_name: string | null;
  start_time: number;
  end_time: number;
  confidence: number | null;
  evidence: EventEvidence[];
};

type IntelligenceItem = {
  title: string;
  summary: string;
  event_ids: string[];
  status?: string | null;
  confidence?: number | null;
};

type MeetingIntelligence = {
  id: string;
  summary: string;
  sections: Record<string, IntelligenceItem[]>;
};

type TranscriptResponse = {
  status: string;
  text: string | null;
  provider: string | null;
  provider_model: string | null;
  media_asset_id: string | null;
  segments: TranscriptSegment[];
};

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function formatTimestamp(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `[${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}]`;
}

export default function MeetingTranscriptPage() {
  const params = useParams<{ id: string }>();
  const [transcript, setTranscript] = useState<TranscriptResponse | null>(null);
  const [speakers, setSpeakers] = useState<Speaker[]>([]);
  const [diarizationStatus, setDiarizationStatus] = useState<DiarizationStatus | null>(null);
  const [events, setEvents] = useState<MeetingEvent[]>([]);
  const [eventStatus, setEventStatus] = useState("NOT_STARTED");
  const [eventFilter, setEventFilter] = useState("ALL");
  const [intelligence, setIntelligence] = useState<MeetingIntelligence | null>(null);
  const [intelligenceStatus, setIntelligenceStatus] = useState("NOT_STARTED");
  const [intelligenceError, setIntelligenceError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeSequence, setActiveSequence] = useState<number | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetch(`${apiUrl}/api/meetings/${params.id}/transcript`, { signal: controller.signal }),
      fetch(`${apiUrl}/api/meetings/${params.id}/speakers`, { signal: controller.signal }),
      fetch(`${apiUrl}/api/meetings/${params.id}/diarization/status`, { signal: controller.signal }),
      fetch(`${apiUrl}/api/meetings/${params.id}/events`, { signal: controller.signal }),
      fetch(`${apiUrl}/api/meetings/${params.id}/events/extraction/status`, { signal: controller.signal }),
      fetch(`${apiUrl}/api/meetings/${params.id}/intelligence`, { signal: controller.signal }),
      fetch(`${apiUrl}/api/meetings/${params.id}/intelligence/status`, { signal: controller.signal }),
    ])
      .then(async ([transcriptResponse, speakersResponse, statusResponse, eventsResponse, eventStatusResponse, intelligenceResponse, intelligenceStatusResponse]) => {
        const transcriptPayload = await transcriptResponse.json();
        const speakersPayload = await speakersResponse.json();
        const statusPayload = await statusResponse.json();
        const eventsPayload = await eventsResponse.json();
        const eventStatusPayload = await eventStatusResponse.json();
        const intelligencePayload = await intelligenceResponse.json();
        const intelligenceStatusPayload = await intelligenceStatusResponse.json();
        if (!transcriptResponse.ok) {
          throw new Error(transcriptPayload.error?.message ?? "Transcript could not be loaded");
        }
        if (!speakersResponse.ok) {
          throw new Error(speakersPayload.error?.message ?? "Speakers could not be loaded");
        }
        if (!statusResponse.ok) {
          throw new Error(statusPayload.error?.message ?? "Diarization status could not be loaded");
        }
        if (!eventsResponse.ok || !eventStatusResponse.ok) {
          throw new Error("Events could not be loaded");
        }
        return [
          transcriptPayload.data as TranscriptResponse,
          speakersPayload.data as Speaker[],
          statusPayload.data as DiarizationStatus,
          eventsPayload.data as MeetingEvent[],
          eventStatusPayload.data.status as string,
          intelligenceResponse.ok ? intelligencePayload.data as MeetingIntelligence : null,
          intelligenceStatusPayload.data.status as string,
          intelligenceStatusPayload.data.error as string | null,
        ] as const;
      })
      .then(([nextTranscript, nextSpeakers, nextStatus, nextEvents, nextEventStatus, nextIntelligence, nextIntelligenceStatus, nextIntelligenceError]) => {
        setTranscript(nextTranscript);
        setSpeakers(nextSpeakers);
        setDiarizationStatus(nextStatus);
        setEvents(nextEvents);
        setEventStatus(nextEventStatus);
        setIntelligence(nextIntelligence);
        setIntelligenceStatus(nextIntelligenceStatus);
        setIntelligenceError(nextIntelligenceError);
      })
      .catch((requestError: Error) => {
        if (requestError.name !== "AbortError") setError(requestError.message);
      })
      .finally(() => setLoading(false));

    return () => controller.abort();
  }, [params.id]);

  useEffect(() => {
    if (eventStatus !== "QUEUED" && eventStatus !== "PROCESSING") return;
    const poll = window.setInterval(async () => {
      const [statusResponse, eventsResponse] = await Promise.all([
        fetch(`${apiUrl}/api/meetings/${params.id}/events/extraction/status`),
        fetch(`${apiUrl}/api/meetings/${params.id}/events`),
      ]);
      if (!statusResponse.ok || !eventsResponse.ok) return;
      const statusPayload = await statusResponse.json();
      const eventsPayload = await eventsResponse.json();
      setEventStatus(statusPayload.data.status);
      setEvents(eventsPayload.data);
    }, 2000);
    return () => window.clearInterval(poll);
  }, [eventStatus, params.id]);

  useEffect(() => {
    if (intelligenceStatus !== "QUEUED" && intelligenceStatus !== "PROCESSING") return;
    const poll = window.setInterval(async () => {
      const [statusResponse, intelligenceResponse] = await Promise.all([
        fetch(`${apiUrl}/api/meetings/${params.id}/intelligence/status`),
        fetch(`${apiUrl}/api/meetings/${params.id}/intelligence`),
      ]);
      if (!statusResponse.ok) return;
      const statusPayload = await statusResponse.json();
      setIntelligenceStatus(statusPayload.data.status);
      setIntelligenceError(statusPayload.data.error);
      if (intelligenceResponse.ok) {
        const intelligencePayload = await intelligenceResponse.json();
        setIntelligence(intelligencePayload.data);
      }
    }, 2000);
    return () => window.clearInterval(poll);
  }, [intelligenceStatus, params.id]);

  const speakerById = new Map(speakers.map((speaker) => [speaker.id, speaker]));
  const seekTo = (segment: TranscriptSegment) => {
    if (!audioRef.current) return;
    audioRef.current.currentTime = segment.start_time;
    void audioRef.current.play().catch(() => undefined);
  };

  const seekToEvidence = (evidence: EventEvidence) => {
    const segment = transcript?.segments.find((candidate) => candidate.id === evidence.transcript_segment_id);
    if (segment) seekTo(segment);
  };

  const seekToEvent = (eventId: string) => {
    const event = events.find((candidate) => candidate.id === eventId);
    if (event?.evidence[0]) seekToEvidence(event.evidence[0]);
  };

  const extractEvents = async () => {
    setEventStatus("QUEUED");
    try {
      const response = await fetch(`${apiUrl}/api/meetings/${params.id}/events/extract`, { method: "POST" });
      if (!response.ok) throw new Error("Event extraction could not be queued");
    } catch {
      setEventStatus("FAILED");
    }
  };

  const generateIntelligence = async () => {
    setIntelligenceStatus("QUEUED");
    setIntelligenceError(null);
    try {
      const response = await fetch(`${apiUrl}/api/meetings/${params.id}/intelligence/generate`, { method: "POST" });
      if (!response.ok) throw new Error((await response.json()).error?.message ?? "Intelligence could not be queued");
    } catch (requestError) {
      setIntelligenceStatus("FAILED");
      setIntelligenceError(requestError instanceof Error ? requestError.message : "Intelligence could not be queued");
    }
  };

  const updatePlayback = () => {
    const currentTime = audioRef.current?.currentTime ?? -1;
    const current = transcript?.segments.find(
      (segment) => currentTime >= segment.start_time && currentTime <= segment.end_time,
    );
    setActiveSequence(current?.sequence ?? null);
  };

  const formatDuration = (seconds: number) => {
    const minutes = Math.floor(seconds / 60);
    return `${minutes}m ${String(Math.floor(seconds % 60)).padStart(2, "0")}s`;
  };

  const eventTypes = ["ALL", "COMMITMENT", "DECISION", "ACTION", "PROPOSAL", "QUESTION", "RISK", "CLAIM", "CONSTRAINT", "ASSUMPTION"];
  const visibleEvents = eventFilter === "ALL" ? events : events.filter((event) => event.event_type === eventFilter);

  return (
    <main className="transcript-page">
      <p className="eyebrow">Meeting transcript</p>
      <h1>Transcript</h1>
      {transcript?.media_asset_id && (
        <audio
          className="transcript-player"
          controls
          onTimeUpdate={updatePlayback}
          ref={audioRef}
          src={`${apiUrl}/api/media/${transcript.media_asset_id}/stream`}
        />
      )}
      {loading && <p role="status">Loading transcript...</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && diarizationStatus && diarizationStatus.status !== "DIARIZATION_COMPLETED" && (
        <p className="diarization-status" role="status">
          Speaker analysis: {diarizationStatus.status.replaceAll("_", " ").toLowerCase()}
          {diarizationStatus.error ? ` - ${diarizationStatus.error}` : ""}
        </p>
      )}
      {!loading && !error && (
        <section className="intelligence-panel" aria-label="Meeting intelligence">
          <div className="intelligence-heading">
            <div>
              <p className="eyebrow">Derived from Phase 6 events</p>
              <h2>Meeting Intelligence</h2>
            </div>
            <button type="button" onClick={generateIntelligence} disabled={intelligenceStatus === "QUEUED" || intelligenceStatus === "PROCESSING" || intelligenceStatus === "COMPLETED"}>
              {intelligenceStatus === "COMPLETED" ? "Intelligence Ready" : "Generate Intelligence"}
            </button>
          </div>
          {intelligenceStatus === "NOT_STARTED" && <p>Extract events before generating meeting intelligence.</p>}
          {(intelligenceStatus === "QUEUED" || intelligenceStatus === "PROCESSING") && <p role="status">Analyzing meeting...</p>}
          {intelligenceStatus === "FAILED" && <p role="alert">{intelligenceError ?? "Intelligence generation failed."}</p>}
          {intelligenceStatus === "COMPLETED" && intelligence && (
            <>
              <div className="intelligence-summary">
                <h3>Executive Summary</h3>
                <p>{intelligence.summary}</p>
              </div>
              {Object.entries({
                decisions: "Key Decisions",
                actions: "Action Items",
                commitments: "Commitments",
                risks: "Risks",
                questions: "Open Questions",
                proposals: "Proposals",
                constraints: "Constraints",
                assumptions: "Assumptions",
                highlights: "Highlights",
                open_items: "Open Items",
              }).map(([key, label]) => {
                const items = intelligence.sections[key] ?? [];
                return (
                  <section className="intelligence-section" key={key}>
                    <h3>{label}</h3>
                    {items.length === 0 ? (
                      <p className="muted-item">No explicit {label.toLowerCase()} were recorded.</p>
                    ) : items.map((item) => (
                      <button className="intelligence-item" key={`${key}-${item.title}`} type="button" onClick={() => seekToEvent(item.event_ids[0])}>
                        <strong>{item.title}</strong>
                        <span>{item.summary}</span>
                        <small>{item.confidence == null ? "" : `confidence ${item.confidence.toFixed(2)} · `}{item.event_ids.length} event reference{item.event_ids.length === 1 ? "" : "s"}</small>
                      </button>
                    ))}
                  </section>
                );
              })}
            </>
          )}
        </section>
      )}
      {!loading && !error && (
        <section className="events-panel" aria-label="Meeting events">
          <div className="events-heading">
            <div>
              <p className="eyebrow">Evidence-backed interpretation</p>
              <h2>Events</h2>
            </div>
            <button type="button" onClick={extractEvents} disabled={eventStatus === "QUEUED" || eventStatus === "PROCESSING" || eventStatus === "COMPLETED"}>
              {eventStatus === "COMPLETED" ? "Events Extracted" : "Extract Events"}
            </button>
          </div>
          <p className="event-status" role="status">{eventStatus.replaceAll("_", " ").toLowerCase()}</p>
          <div className="event-filters" role="group" aria-label="Event filters">
            {eventTypes.map((type) => (
              <button className={eventFilter === type ? "is-selected" : ""} key={type} type="button" onClick={() => setEventFilter(type)}>
                {type === "ALL" ? "All" : type.toLowerCase()}
              </button>
            ))}
          </div>
          {eventStatus === "COMPLETED" && visibleEvents.length === 0 && (
            <p>No structured events were detected in this meeting.</p>
          )}
          {eventStatus !== "COMPLETED" && events.length === 0 && (
            <p>No events extracted yet.</p>
          )}
          <div className="event-list">
            {visibleEvents.map((event) => (
              <article className="event-card" key={event.id}>
                <div className="event-card-heading">
                  <strong>{event.event_type}</strong>
                  <time>{formatTimestamp(event.start_time)}</time>
                </div>
                <h3>{event.title}</h3>
                <p>{event.subject}{event.value ? `: ${event.value}` : ""}</p>
                <small>{event.speaker_display_name ?? event.speaker_label ?? "Unassigned speaker"} · confidence {event.confidence?.toFixed(2) ?? "n/a"}</small>
                <div className="event-evidence">
                  {event.evidence.map((evidence) => (
                    <button key={evidence.id} type="button" onClick={() => seekToEvidence(evidence)}>
                      {formatTimestamp(evidence.start_time)} &quot;{evidence.text}&quot;
                    </button>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
      {!loading && !error && transcript?.status !== "TRANSCRIBED" && (
        <p role="status">Transcription status: {transcript?.status ?? "not available"}</p>
      )}
      {!loading && !error && transcript?.status === "TRANSCRIBED" && transcript.segments.length === 0 && (
        <p>No transcript segments are available.</p>
      )}
      {!loading && !error && transcript?.segments.length ? (
        <>
          <div className="speaker-panel" aria-label="Speakers">
            <h2>Speakers</h2>
            {speakers.length === 0 && <p>No speakers identified yet.</p>}
            {speakers.map((speaker, index) => (
              <div className="speaker-summary" key={speaker.id}>
                <span className={`speaker-dot speaker-dot-${index % 4}`} aria-hidden="true" />
                <div>
                  <strong>{speaker.label}</strong>
                  {speaker.display_name && <span>{speaker.display_name}</span>}
                  <small>
                    {speaker.segments_count} segments · {formatDuration(speaker.speaking_duration_seconds)}
                  </small>
                </div>
              </div>
            ))}
          </div>
          <section aria-label="Timestamped transcript" className="transcript-list">
            {transcript.segments.map((segment) => (
              <article
                className={`transcript-segment${activeSequence === segment.sequence ? " is-active" : ""}`}
                key={segment.id}
                onClick={() => seekTo(segment)}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") seekTo(segment);
                }}
              >
                <time dateTime={`PT${segment.start_time}S`}>{formatTimestamp(segment.start_time)}</time>
                <div>
                  <strong className={`speaker-label speaker-label-${segment.sequence % 4}`}>
                    {segment.speaker_id ? speakerById.get(segment.speaker_id)?.label ?? "Unknown speaker" : "Unassigned"}
                  </strong>
                  <p>{segment.text}</p>
                </div>
              </article>
            ))}
          </section>
        </>
      ) : null}
    </main>
  );
}
