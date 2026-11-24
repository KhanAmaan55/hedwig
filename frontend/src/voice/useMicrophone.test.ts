import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useMicrophone, type ListenApi } from "./useMicrophone";

class FakeRecorder {
  static made: FakeRecorder[] = [];
  readonly mimeType = "audio/webm";
  recording = false;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  constructor(readonly stream: MediaStream) {
    FakeRecorder.made.push(this);
  }
  start(): void {
    this.recording = true;
  }
  stop(): void {
    this.recording = false;
    this.onstop?.();
  }
}

function api(): ListenApi {
  return {
    speechStatus: vi.fn().mockResolvedValue({ listening: true }),
    transcribe: vi.fn().mockResolvedValue({ text: "water the tomatoes" }),
  };
}

function microphoneThatAnswersLater() {
  let grant: () => void = () => undefined;
  const stream = { getTracks: () => [{ stop: vi.fn() }] } as unknown as MediaStream;
  const getUserMedia = vi.fn(
    () =>
      new Promise<MediaStream>((resolve) => {
        grant = () => {
          resolve(stream);
        };
      }),
  );
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  return {
    grant: () => {
      grant();
    },
    getUserMedia,
  };
}

afterEach(() => {
  FakeRecorder.made = [];
  vi.unstubAllGlobals();
});

describe("useMicrophone (VOI-02, docs/15 §14)", () => {
  it("a release while the microphone is starting stops the one recording it starts", async () => {
    const permission = microphoneThatAnswersLater();
    const { result } = renderHook(() => useMicrophone(api()));

    let started: Promise<boolean> = Promise.resolve(false);
    let heard: Promise<string | null> = Promise.resolve(null);
    act(() => {
      started = result.current.start();
      heard = result.current.stop(); // released before permission came back
    });
    await act(async () => {
      permission.grant();
      await started;
    });

    await expect(heard).resolves.toBe("water the tomatoes");
    expect(FakeRecorder.made).toHaveLength(1);
    expect(FakeRecorder.made[0]?.recording).toBe(false);
    await waitFor(() => {
      expect(result.current.state).toBe("idle");
    });
  });

  it("a second start while starting does not open a second recorder", async () => {
    const permission = microphoneThatAnswersLater();
    const { result } = renderHook(() => useMicrophone(api()));

    let first: Promise<boolean> = Promise.resolve(false);
    act(() => {
      first = result.current.start();
      void result.current.start();
    });
    await act(async () => {
      permission.grant();
      await first;
    });

    expect(permission.getUserMedia).toHaveBeenCalledTimes(1);
    expect(FakeRecorder.made).toHaveLength(1);
  });

  it("a refused microphone leaves it idle, and a stop waiting on it gets nothing", async () => {
    vi.stubGlobal("navigator", {
      mediaDevices: { getUserMedia: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    const { result } = renderHook(() => useMicrophone(api()));

    let started: Promise<boolean> = Promise.resolve(true);
    let heard: Promise<string | null> = Promise.resolve("x");
    await act(async () => {
      started = result.current.start();
      heard = result.current.stop();
      await started;
    });

    await expect(started).resolves.toBe(false);
    await expect(heard).resolves.toBeNull();
    expect(result.current.state).toBe("idle");
    expect(result.current.error).toMatch(/not available/);
  });
});
