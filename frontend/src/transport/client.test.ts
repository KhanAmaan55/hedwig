import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, HedwigClient, resolveBaseUrl } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("resolveBaseUrl", () => {
  it("prefers the URL injected by the desktop shell", () => {
    vi.stubGlobal("window", { hedwig: { apiBaseUrl: "http://127.0.0.1:9000/" } });
    expect(resolveBaseUrl()).toBe("http://127.0.0.1:9000");
  });

  it("falls back to relative URLs so Vite can proxy in the browser", () => {
    vi.stubGlobal("window", {});
    expect(resolveBaseUrl()).toBe("");
  });
});

describe("ApiError", () => {
  it("carries the server's stable code and correlation id", async () => {
    const response = new Response(
      JSON.stringify({
        error: {
          code: "capability_unavailable",
          message: "The conversational model is not available.",
          detail: { tier: "conversational" },
          correlation_id: "req_01JQ",
          retryable: true,
        },
      }),
      { status: 503, headers: { "x-correlation-id": "req_01JQ" } },
    );

    const error = await ApiError.fromResponse(response);

    expect(error.code).toBe("capability_unavailable");
    expect(error.retryable).toBe(true);
    expect(error.correlationId).toBe("req_01JQ");
  });

  it("degrades gracefully when the body is not our envelope", async () => {
    const error = await ApiError.fromResponse(new Response("<html>502</html>", { status: 502 }));
    expect(error.code).toBe("internal");
    expect(error.retryable).toBe(true);
  });
});

describe("HedwigClient", () => {
  it("never sends credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await new HedwigClient("http://127.0.0.1:8730").health();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8730/v1/health");
    expect(init.credentials).toBe("omit");
  });

  it("throws ApiError on a failed request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 404, headers: new Headers() })),
    );

    await expect(new HedwigClient("").health()).rejects.toBeInstanceOf(ApiError);
  });
});
