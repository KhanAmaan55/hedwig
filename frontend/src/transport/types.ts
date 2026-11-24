/**
 * Wire types for the HEDWIG API.
 *
 * Hand-written for Milestone 1. From the milestone that ships the full REST surface these
 * are generated from the backend's checked-in OpenAPI schema, so a backend change that
 * breaks the client fails the build rather than the browser (docs/18 §8).
 */

export type SubsystemStatus = "ok" | "degraded" | "down";

export interface Subsystem {
  status: SubsystemStatus;
  detail: Record<string, unknown>;
}

export interface Notice {
  level: "info" | "warn" | "error";
  code: string;
  message: string;
}

/** `GET /v1/health` (docs/19 §7). */
export interface Health {
  status: SubsystemStatus;
  version: string;
  schema_version: number | null;
  environment: string;
  started_at: string;
  uptime_s: number;
  subsystems: Record<string, Subsystem>;
  notices: Notice[];
}

/** The single error envelope every endpoint uses (docs/16 §4.6). */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    detail: Record<string, unknown>;
    correlation_id: string | null;
    retryable: boolean;
  };
}

/** The nameable part of an expression (docs/15 §4.1). Nothing negative exists to reach for. */
export type Overlay = "interest" | "warmth" | "amusement" | "uncertainty" | "fatigue";

/**
 * `GET /v1/mind/expression` (docs/28 §4).
 *
 * Semantic, never geometric: what the face should express, not how any particular model does
 * it. Each renderer maps this through a character profile (ADR-0021).
 */
export interface ExpressionIntent {
  mood: { valence: number; arousal: number };
  overlay: Partial<Record<Overlay, number>>;
  gaze: "user" | "away" | "thinking";
  energy: number;
  action: string;
  /** Changes whenever the underlying emotional state does. */
  emotion_version: number | null;
}

// --- speech (docs/15 §13) ----------------------------------------------------

export interface SpeechStatus {
  available: boolean;
  engine: string;
  voice: string;
  reason: string;
  /** Whether HEDWIG can hear: a speech-recognition model is installed (VOI-02). */
  listening?: boolean;
  listening_reason?: string;
}

// --- conversation (docs/16 §12) ---------------------------------------------

/** `POST /v1/sessions`, `GET /v1/sessions/{id}`. */
export interface SessionInfo {
  id: string;
  channel: string;
  started_at: string;
  ended_at: string | null;
  end_reason: string | null;
  turn_count: number;
  last_active_at: string;
  is_open: boolean;
}

/** `POST /v1/sessions/{id}/messages`: one completed turn. */
export interface TurnReply {
  turn_id: string;
  session_id: string;
  user_message_id: string;
  message_id: string;
  reply: string;
  status: "completed" | "refused" | "truncated";
  intent: string | null;
  /** Memories shown to the model for this reply. */
  cited_memory_ids: string[];
  mood: Record<string, number>;
}

export interface HistoryMessage {
  id: string;
  seq: number;
  role: "user" | "hedwig" | "system" | "tool";
  text: string;
  created_at: string;
}

/** `GET /v1/sessions/{id}/messages`: oldest first. */
export interface MessagePage {
  messages: HistoryMessage[];
  next_before: string | null;
}

// --- the Mind Inspector (UX-03) -------------------------------------------------

export interface MemoryItem {
  id: string;
  kind: "episode" | "belief";
  text: string;
  occurred_at: string;
  salience: number;
  confidence: number;
  pinned: boolean;
  forgotten: boolean;
  /** Whose word it rests on: user, self, untrusted… (docs/13 §3). */
  trust: string;
  source: string | null;
  status: string | null;
  /** Beliefs only: what replaced this one, when it was corrected or superseded. */
  replaced_by?: string | null;
}

export interface MemorySearch {
  query: string;
  hits: { memory: MemoryItem; score: number; breakdown: Record<string, number> }[];
}

export interface Goal {
  id: string;
  description: string;
  kind: string;
  status: "proposed" | "active" | "blocked" | "done" | "abandoned";
  priority: number;
  progress: number;
  context: string;
  completion: string;
  origin: string;
  created_at: string;
  last_touched_at: string;
}

export interface Gap {
  id: string;
  question: string;
  origin: string;
  priority: number;
  status: string;
  attempts: number;
  created_at: string;
}

/** Two beliefs the nightly run thinks repeat or conflict: a proposal, not a change (docs/12 §15). */
export interface BeliefDispute {
  pair: string;
  verdict: "same" | "contradicts";
  proposed_at: string;
  first: { id: string; statement: string };
  second: { id: string; statement: string };
}

/** One entry in a project's journal (ADR-0025). */
export interface GoalNote {
  id: string;
  kind: "decision" | "progress" | "question" | "blocker" | "done";
  text: string;
  source: "system" | "reflection" | "user";
  session_id: string | null;
  created_at: string;
}

/** The folders HEDWIG reads, and every file in them (docs/13 §14). */
export interface DocumentListing {
  roots: { path: string; added_at: string }[];
  documents: {
    id: string;
    root: string;
    path: string;
    title: string;
    chunk_count: number;
    removed_at: string | null;
  }[];
}

export interface IngestReport {
  added: number;
  updated: number;
  unchanged: number;
  removed: number;
  skipped: { path: string; reason: string }[];
}

export type PurgeScope = "memory" | "session" | "phrase";

/** What erasing would remove. Nothing has changed yet (docs/21 §7.4). */
export interface PurgePreview {
  scope: PurgeScope;
  counts: Record<string, number>;
  examples: string[];
  backups: number;
  confirm: string | null;
}

/** That an erasure happened, and how much — never what. */
export interface PurgeReceipt {
  id: string;
  scope_kind: PurgeScope;
  created_at: string;
  counts: Record<string, number>;
  verified: "file" | "query" | "failed";
  also_elsewhere: number;
  backups_deleted: number;
}

export type DisputeChoice = "first" | "second" | "both" | "same";

export interface Finding {
  id: string;
  question: string;
  summary: string;
  sources: { title?: string; url: string }[];
  status: string;
  trust: string;
  created_at: string;
}

export interface EmotionReading {
  happiness: number;
  trust: number;
  curiosity: number;
  confidence: number;
  energy: number;
  stress: number;
  valence: number;
  arousal: number;
}

export interface PersonalityReading {
  traits: { trait: string; value: number; anchor: number; lifetime_drift: number }[];
  version: number;
}

/** The monthly identity report, assembled from what is true now (docs/12 §16). */
export interface IdentityReport {
  month: string;
  created_at: string;
  seen_at: string | null;
  summary: string | null;
  personality: PersonalityChange[];
  relationship: {
    familiarity?: number;
    trust?: number;
    turns: number;
    conversations: number;
  };
  memory: {
    episodes_formed: number;
    beliefs_formed: number;
    merged: number;
    faded: number;
    forgotten_by_you: number;
    remembered_now: number;
  };
  goals: { finished: string[]; dropped: string[]; started: string[] };
  reactions: { trait: string; wanted: "more" | "less"; times: number }[];
}

/** One change to a trait, and why (docs/10 §5.5, §13). */
export interface PersonalityChange {
  id: string;
  trait: string;
  delta: number;
  source: string;
  applied_at: string;
  reverted_at: string | null;
  rationale: string | null;
}

export interface RelationshipReading {
  familiarity: number;
  affinity: number;
  trust: number;
  interaction_count: number;
  last_interaction_at: string | null;
}

/** `GET /v1/explain/{message_id}` (docs/16 §14). Absent things are null or empty, never missing. */
export interface Explanation {
  message_id: string;
  question: string | null;
  reply: string;
  intent: string | null;
  rationale: string | null;
  /** null for replies from before directives were recorded. */
  directives: string[] | null;
  goals_in_view: number | null;
  finding_offered: boolean | null;
  mood: Record<string, number> | null;
  retrieval: {
    queries: string[];
    items: {
      memory_id: string;
      score: number;
      trust: string;
      kind: string | null;
      text: string | null;
      forgotten_since: boolean;
    }[];
    dropped_count: number;
  } | null;
  tools: { name: string; status: string; error: string | null; duration_ms: number | null }[];
  model: {
    purpose: string;
    model: string;
    status: string;
    tokens_in: number | null;
    tokens_out: number | null;
    duration_ms: number | null;
  }[];
  trace_id: string | null;
}
