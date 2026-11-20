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

describe("the API token (ADR-0022)", () => {
  function ok(body: unknown): Response {
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }

  function headersOf(call: unknown[]): Record<string, string> {
    return (call[1] as RequestInit).headers as Record<string, string>;
  }

  it("is sent on conversation routes and not on health", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(ok({ id: "sess_1" })));
    vi.stubGlobal("fetch", fetchMock);
    const client = new HedwigClient("http://h", () => Promise.resolve("tok"));

    await client.health();
    await client.openSession();

    expect(headersOf(fetchMock.mock.calls[0] as unknown[]).Authorization).toBeUndefined();
    expect(headersOf(fetchMock.mock.calls[1] as unknown[]).Authorization).toBe("Bearer tok");
    expect((fetchMock.mock.calls[1] as unknown[])[0]).toBe("http://h/v1/sessions");
  });

  it("is left to the Vite proxy when the page has none", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(ok({ id: "sess_1" })));
    vi.stubGlobal("fetch", fetchMock);

    await new HedwigClient("", () => Promise.resolve(null)).openSession();

    expect(headersOf(fetchMock.mock.calls[0] as unknown[]).Authorization).toBeUndefined();
  });

  it("is asked for again, once, after a 401 — which is how a rotation is picked up", async () => {
    const unauthorized = () =>
      new Response(JSON.stringify({ error: { code: "unauthorized", message: "no" } }), {
        status: 401,
      });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(ok({ id: "sess_1" }));
    vi.stubGlobal("fetch", fetchMock);
    const tokens = vi.fn((refresh: boolean) => Promise.resolve(refresh ? "new" : "old"));

    const session = await new HedwigClient("http://h", tokens).openSession();

    expect(session.id).toBe("sess_1");
    expect(tokens.mock.calls).toEqual([[false], [true]]);
    expect(headersOf(fetchMock.mock.calls[1] as unknown[]).Authorization).toBe("Bearer new");
  });

  it("gives up after one retry", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({ error: { code: "unauthorized", message: "no" } }), {
            status: 401,
          }),
        ),
      ),
    );
    await expect(
      new HedwigClient("http://h", () => Promise.resolve("t")).send("s", "hi"),
    ).rejects.toMatchObject({ code: "unauthorized" });
  });

  it("carries the error detail, so a failed turn's reply reaches the pane", async () => {
    const error = await ApiError.fromResponse(
      new Response(
        JSON.stringify({
          error: {
            code: "capability_unavailable",
            message: "down",
            detail: { reply: "I can't think right now." },
            correlation_id: null,
            retryable: true,
          },
        }),
        { status: 503 },
      ),
    );
    expect(error.detail.reply).toBe("I can't think right now.");
  });
});
