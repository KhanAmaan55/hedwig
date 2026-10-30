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
