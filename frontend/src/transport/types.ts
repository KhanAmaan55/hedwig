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
