import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import type { Health } from "./transport/types";

const HEALTHY: Health = {
  status: "ok",
  version: "0.1.0",
  schema_version: null,
  environment: "test",
  started_at: "2026-01-01T09:00:00.000+00:00",
  uptime_s: 3725,
  subsystems: {
    api: { status: "ok", detail: {} },
    storage: { status: "ok", detail: { present: true } },
  },
  notices: [],
};

function mockFetch(response: Partial<Response> & { json?: () => Promise<unknown> }): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: () => Promise.resolve(HEALTHY),
      ...response,
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("App", () => {
  it("renders the backend's health once connected", async () => {
    mockFetch({});
    render(<App />);

    expect(await screen.findByText("backend connected")).toBeInTheDocument();
    expect(screen.getByText("0.1.0")).toBeInTheDocument();
    expect(screen.getByText("1h 02m")).toBeInTheDocument();
    expect(screen.getByText("storage")).toBeInTheDocument();
  });

  it("shows a recoverable error when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Backend unreachable");
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("surfaces notices from a degraded backend", async () => {
    mockFetch({
      json: () =>
        Promise.resolve({
          ...HEALTHY,
          status: "degraded",
          subsystems: { storage: { status: "degraded", detail: {} } },
          notices: [
            { level: "warn", code: "data_dir_missing", message: "Data directory is missing." },
          ],
        } satisfies Health),
    });
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Data directory is missing.")).toBeInTheDocument();
    });
    expect(screen.getByText("degraded")).toBeInTheDocument();
  });
});
