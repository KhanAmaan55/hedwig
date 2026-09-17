/**
 * The HTTP client.
 *
 * Isolated in `transport/` so another client — a terminal UI, a mobile app, a future
 * avatar renderer — can reuse it unchanged (docs/18 §4). Nothing above this directory
 * knows about fetch, URLs, or the error envelope.
 */

import type { ApiErrorBody, Health } from "./types";

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

/** A failed request, carrying the server's stable error code and correlation id. */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly correlationId: string | null,
    readonly retryable: boolean,
  ) {
    super(message);
    this.name = "ApiError";
  }

  static async fromResponse(response: Response): Promise<ApiError> {
    let code = "internal";
    let message = response.statusText || "Request failed";
    let retryable = response.status >= 500;

    try {
      const body = (await response.json()) as Partial<ApiErrorBody>;
      if (body.error) {
        code = body.error.code;
        message = body.error.message;
        retryable = body.error.retryable;
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
    );
  }
}

export class HedwigClient {
  constructor(private readonly baseUrl: string = resolveBaseUrl()) {}

  async health(signal?: AbortSignal): Promise<Health> {
    return this.get<Health>("/v1/health", signal);
  }

  private async get<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      // No cookies, ever: cookie auth would be sent automatically by any page on
      // localhost, which is precisely the attack the token design avoids (docs/21 §4).
      credentials: "omit",
      ...(signal ? { signal } : {}),
    });

    if (!response.ok) throw await ApiError.fromResponse(response);
    return (await response.json()) as T;
  }
}

export const client = new HedwigClient();
