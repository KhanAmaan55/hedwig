/**
 * The HTTP client.
 *
 * Isolated in `transport/` so another client — a terminal UI, a mobile app, a future
 * avatar renderer — can reuse it unchanged (docs/18 §4). Nothing above this directory
 * knows about fetch, URLs, or the error envelope.
 */

import type {
  ApiErrorBody,
  BeliefDispute,
  DisputeChoice,
  EmotionReading,
  Explanation,
  ExpressionIntent,
  Finding,
  Gap,
  Goal,
  Health,
  MemoryItem,
  MemorySearch,
  PersonalityChange,
  PersonalityReading,
  PurgePreview,
  PurgeReceipt,
  PurgeScope,
  RelationshipReading,
  MessagePage,
  SessionInfo,
  SpeechStatus,
  TurnReply,
} from "./types";

/**
 * Where the backend is.
 *
 * - Desktop shell: an absolute loopback URL, injected by the preload script.
 * - Browser dev:   empty, so requests are relative and Vite proxies `/v1`.
 */
export function resolveBaseUrl(): string {
  if (typeof window !== "undefined" && window.hedwig?.apiBaseUrl) {
    return window.hedwig.apiBaseUrl.replace(/\/$/, "");
  }
  const configured = import.meta.env.VITE_HEDWIG_API;
  return configured ? configured.replace(/\/$/, "") : "";
}

/**
 * Where the API token comes from (ADR-0022, docs/18 §13.2).
 *
 * - Desktop shell: the preload's `readToken`, which asks the main process.
 * - Browser dev:   nothing. The Vite proxy adds the header, so the page never holds it.
 */
export type TokenSource = (refresh: boolean) => Promise<string | null>;

export function resolveTokenSource(): TokenSource {
  const shell = typeof window !== "undefined" ? window.hedwig : undefined;
  const readToken = shell?.readToken;
  if (readToken) return (refresh) => readToken(refresh);
  return () => Promise.resolve(null);
}

/** A failed request, carrying the server's stable error code and correlation id. */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly correlationId: string | null,
    readonly retryable: boolean,
    /** The envelope's `detail` — e.g. HEDWIG's own sentence when a turn failed. */
    readonly detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }

  static async fromResponse(response: Response): Promise<ApiError> {
    let code = "internal";
    let message = response.statusText || "Request failed";
    let retryable = response.status >= 500;
    let detail: Record<string, unknown> = {};

    try {
      const body = (await response.json()) as Partial<ApiErrorBody>;
      if (body.error) {
        code = body.error.code;
        message = body.error.message;
        retryable = body.error.retryable;
        detail = body.error.detail;
      }
    } catch {
      // A response that is not our envelope is still an error; keep the status text.
    }

    return new ApiError(
      code,
      message,
      response.status,
      response.headers.get("x-correlation-id"),
      retryable,
      detail,
    );
  }
}

interface RequestOptions {
  readonly body?: unknown;
  readonly signal?: AbortSignal | undefined;
  /** User-content routes need the token; health and HEDWIG's own state do not (ADR-0022). */
  readonly auth?: boolean;
  /** The body is bytes of this media type, not JSON. */
  readonly binary?: string;
}

export class HedwigClient {
  constructor(
    private readonly baseUrl: string = resolveBaseUrl(),
    private readonly tokenSource: TokenSource = resolveTokenSource(),
  ) {}

  async health(signal?: AbortSignal): Promise<Health> {
    return this.request<Health>("GET", "/v1/health", { signal });
  }

  /** What the avatar should be expressing (docs/28 §4). */
  async expression(signal?: AbortSignal): Promise<ExpressionIntent> {
    return this.request<ExpressionIntent>("GET", "/v1/mind/expression", { signal });
  }

  // -- conversation (docs/16 §12) ----------------------------------------------

  async openSession(channel: "web" | "api" | "cli" = "web"): Promise<SessionInfo> {
    return this.request<SessionInfo>("POST", "/v1/sessions", { body: { channel }, auth: true });
  }

  /** End the conversation now; its relationship update need not wait for the idle timeout
   *  (FND-03). Idempotent on the server. */
  async endSession(sessionId: string): Promise<SessionInfo> {
    return this.request<SessionInfo>("POST", `/v1/sessions/${encodeURIComponent(sessionId)}/end`, {
      auth: true,
    });
  }

  /** Last `limit` messages, oldest first. */
  async history(sessionId: string, limit = 50): Promise<MessagePage> {
    const query = new URLSearchParams({ limit: String(limit) });
    return this.request<MessagePage>(
      "GET",
      `/v1/sessions/${encodeURIComponent(sessionId)}/messages?${query.toString()}`,
      { auth: true },
    );
  }

  /** Say something; resolves with the complete reply (non-streaming, docs/16 §12.3). */
  async send(sessionId: string, text: string): Promise<TurnReply> {
    return this.request<TurnReply>(
      "POST",
      `/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
      { body: { text }, auth: true },
    );
  }

  /** Why HEDWIG said that (docs/16 §14). */
  async explain(messageId: string): Promise<Explanation> {
    return this.request<Explanation>("GET", `/v1/explain/${encodeURIComponent(messageId)}`, {
      auth: true,
    });
  }

  // -- the Mind Inspector (UX-03) -----------------------------------------------

  async emotion(): Promise<EmotionReading> {
    return this.request<EmotionReading>("GET", "/v1/mind/emotion");
  }

  async personality(): Promise<PersonalityReading> {
    return this.request<PersonalityReading>("GET", "/v1/mind/personality", { auth: true });
  }

  async personalityHistory(limit = 8): Promise<PersonalityChange[]> {
    return this.request<PersonalityChange[]>(
      "GET",
      `/v1/mind/personality/history?limit=${String(limit)}`,
      { auth: true },
    );
  }

  async revertChange(id: string): Promise<void> {
    await this.request<unknown>("POST", `/v1/mind/personality/revert/${encodeURIComponent(id)}`, {
      auth: true,
    });
  }

  async relationship(): Promise<RelationshipReading> {
    return this.request<RelationshipReading>("GET", "/v1/mind/relationship", { auth: true });
  }

  async memories(includeForgotten = false): Promise<MemoryItem[]> {
    const query = includeForgotten ? "?limit=100&include_forgotten=true" : "?limit=100";
    const body = await this.request<{ memories: MemoryItem[] }>("GET", `/v1/memory${query}`, {
      auth: true,
    });
    return body.memories;
  }

  async searchMemory(q: string): Promise<MemorySearch> {
    const query = new URLSearchParams({ q, limit: "10" });
    return this.request<MemorySearch>("GET", `/v1/memory/search?${query.toString()}`, {
      auth: true,
    });
  }

  async memoryAction(
    id: string,
    action: "pin" | "unpin" | "forget" | "restore",
  ): Promise<MemoryItem> {
    const path = `/v1/memory/${encodeURIComponent(id)}/${action === "unpin" ? "pin" : action}`;
    return this.request<MemoryItem>(action === "unpin" ? "DELETE" : "POST", path, { auth: true });
  }

  /** Replace a memory with the user's correction; returns the replacement (docs/06 §18). */
  async correctMemory(
    id: string,
    correction: { text?: string; confidence?: number },
  ): Promise<MemoryItem> {
    return this.request<MemoryItem>("PATCH", `/v1/memory/${encodeURIComponent(id)}`, {
      body: correction,
      auth: true,
    });
  }

  async disputes(): Promise<BeliefDispute[]> {
    const body = await this.request<{ disputes: BeliefDispute[] }>(
      "GET",
      "/v1/reflection/disputes",
      { auth: true },
    );
    return body.disputes;
  }

  async resolveDispute(pair: string, choice: DisputeChoice): Promise<void> {
    await this.request<unknown>(
      "POST",
      `/v1/reflection/disputes/${encodeURIComponent(pair)}/resolve`,
      { body: { choice }, auth: true },
    );
  }

  async goals(): Promise<Goal[]> {
    const query = "?status=proposed&status=active&status=blocked";
    const body = await this.request<{ goals: Goal[] }>("GET", `/v1/goals${query}`, { auth: true });
    return body.goals;
  }

  async createGoal(description: string): Promise<Goal> {
    return this.request<Goal>("POST", "/v1/goals", { body: { description }, auth: true });
  }

  async updateGoal(id: string, changes: Partial<Pick<Goal, "status" | "progress">>): Promise<Goal> {
    return this.request<Goal>("PATCH", `/v1/goals/${encodeURIComponent(id)}`, {
      body: changes,
      auth: true,
    });
  }

  async gaps(): Promise<Gap[]> {
    return this.request<Gap[]>("GET", "/v1/curiosity/gaps", { auth: true });
  }

  async askCuriosity(question: string): Promise<Gap> {
    return this.request<Gap>("POST", "/v1/curiosity/gaps", { body: { question }, auth: true });
  }

  async findings(): Promise<Finding[]> {
    return this.request<Finding[]>("GET", "/v1/curiosity/findings", { auth: true });
  }

  async findingAction(id: string, action: "confirm" | "dismiss"): Promise<Finding> {
    return this.request<Finding>(
      "POST",
      `/v1/curiosity/findings/${encodeURIComponent(id)}/${action}`,
      { auth: true },
    );
  }

  // -- speech (docs/15 §13) -----------------------------------------------------

  async speechStatus(): Promise<SpeechStatus> {
    return this.request<SpeechStatus>("GET", "/v1/speech", { auth: true });
  }

  /** The reply as audio/wav. */
  async speak(text: string, signal?: AbortSignal): Promise<ArrayBuffer> {
    return this.request<ArrayBuffer>("POST", "/v1/speech", {
      body: { text },
      auth: true,
      signal,
      binary: "audio/wav",
    });
  }

  // -- privacy (docs/21 §7.4) -----------------------------------------------------

  /** Everything HEDWIG holds, as a zip. */
  async exportAll(): Promise<ArrayBuffer> {
    return this.request<ArrayBuffer>("POST", "/v1/export", {
      auth: true,
      binary: "application/zip",
    });
  }

  async purgePreview(scope: PurgeScope, value: string): Promise<PurgePreview> {
    return this.request<PurgePreview>("POST", "/v1/purge/preview", {
      body: { scope, value },
      auth: true,
    });
  }

  async purge(scope: PurgeScope, value: string, confirm: string): Promise<PurgeReceipt> {
    return this.request<PurgeReceipt>("POST", "/v1/purge", {
      body: { scope, value, confirm },
      auth: true,
    });
  }

  // -- transport ---------------------------------------------------------------

  private async request<T>(
    method: "GET" | "POST" | "PATCH" | "DELETE",
    path: string,
    options: RequestOptions = {},
    refreshToken = false,
  ): Promise<T> {
    const headers: Record<string, string> = {
      Accept: options.binary ?? "application/json",
    };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (options.auth) {
      const token = await this.tokenSource(refreshToken);
      if (token) headers.Authorization = `Bearer ${token}`;
    }

    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers,
      // No cookies, ever: cookie auth would be sent automatically by any page on
      // localhost, which is precisely the attack the token design avoids (docs/21 §4).
      credentials: "omit",
      ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
      ...(options.signal ? { signal: options.signal } : {}),
    });

    // A rotated token: ask for it again, once (docs/18 §13.2).
    if (response.status === 401 && options.auth && !refreshToken) {
      return this.request<T>(method, path, options, true);
    }
    if (!response.ok) throw await ApiError.fromResponse(response);
    if (options.binary) return (await response.arrayBuffer()) as T;
    return (await response.json()) as T;
  }
}

export const client = new HedwigClient();
