import { afterEach, describe, expect, it, vi } from "vitest";

import { activityStore } from "../stores/activity";

import { speakSilently } from "./player";

afterEach(() => {
  vi.useRealTimers();
  activityStore.set("idle");
});

describe("speakSilently", () => {
  it("moves the mouth at the pace of the text, then stops on its own", async () => {
    vi.useFakeTimers();
    const utterance = speakSilently("Hello there, how are you?"); // ~2 s at 12 chars/s
    vi.advanceTimersByTime(300);
    expect(activityStore.get()).toBe("speaking");
    expect(activityStore.mouth()).toBeGreaterThan(0);

    vi.advanceTimersByTime(3000);
    await utterance.done;
    expect(activityStore.get()).toBe("idle");
  });

  it("stops at once when asked", async () => {
    vi.useFakeTimers();
    const utterance = speakSilently("A long reply ".repeat(50));
    vi.advanceTimersByTime(200);
    utterance.stop();
    await utterance.done;
    expect(activityStore.get()).toBe("idle");
    expect(activityStore.mouth()).toBe(0);
  });
});
