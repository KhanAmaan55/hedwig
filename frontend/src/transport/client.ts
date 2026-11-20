/**
 * The HTTP client.
 *
 * Isolated in `transport/` so another client — a terminal UI, a mobile app, a future
 * avatar renderer — can reuse it unchanged (docs/18 §4). Nothing above this directory
 * knows about fetch, URLs, or the error envelope.
 */

import type {
  ApiErrorBody,
  ExpressionIntent,
  Health,
  MessagePage,
  SessionInfo,
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

  // -- transport ---------------------------------------------------------------

  private async request<T>(
    method: "GET" | "POST",
    path: string,
    options: RequestOptions = {},
    refreshToken = false,
  ): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
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
    return (await response.json()) as T;
  }
}

export const client = new HedwigClient();
