import { describe, expect, it } from "vitest";

import { activityStore } from "../stores/activity";

import type { Utterance } from "./player";
import { SentenceQueue } from "./queue";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

function harness() {
  const asked: string[] = [];
  const played: string[] = [];
  const stopped: string[] = [];
  const pending = new Map<string, (audio: ArrayBuffer) => void>();
  const signals: AbortSignal[] = [];
  const endings = new Map<string, () => void>();
  const queue = new SentenceQueue(
    (text, signal) => {
      asked.push(text);
      signals.push(signal);
      return new Promise<ArrayBuffer>((resolve) => pending.set(text, resolve));
    },
    (_audio, text): Utterance => {
      played.push(text);
      let finish: () => void = () => undefined;
      const done = new Promise<void>((resolve) => {
        finish = resolve;
      });
      endings.set(text, finish);
      return {
        done,
        stop: () => {
          stopped.push(text);
          finish();
        },
      };
    },
  );
  const synthesised = (text: string) => pending.get(text)?.(new ArrayBuffer(8));
  const ended = (text: string) => endings.get(text)?.();
  return { queue, asked, played, stopped, signals, synthesised, ended };
}

describe("SentenceQueue (docs/15 §14)", () => {
  it("synthesises each sentence once it is complete, one at a time, and plays in order", async () => {
    const h = harness();
    h.queue.feed("Porto is lovely in June. The trai");
    await tick();
    expect(h.asked).toEqual(["Porto is lovely in June."]);
    h.queue.feed("n from Lisbon takes three hours. And");
    await tick();
    expect(h.asked).toEqual(["Porto is lovely in June."]); // the next waits for this synthesis

    h.synthesised("Porto is lovely in June.");
    await tick();
    expect(h.played).toEqual(["Porto is lovely in June."]);
    expect(h.asked).toEqual([
      "Porto is lovely in June.",
      "The train from Lisbon takes three hours.",
    ]);
    expect(activityStore.voiced()).toBe(true);

    h.synthesised("The train from Lisbon takes three hours."); // ready while the first plays
    await tick();
    expect(h.played).toEqual(["Porto is lovely in June."]);
    h.ended("Porto is lovely in June.");
    await tick();
    expect(h.played).toEqual([
      "Porto is lovely in June.",
      "The train from Lisbon takes three hours.",
    ]);
    h.queue.stop();
  });

  it("says what the final reply has beyond what was sent, once", async () => {
    const h = harness();
    h.queue.feed("Porto is lovely in June. Book the ");
    const done = h.queue.finish("Porto is lovely in June. Book the train early.");
    await tick();
    h.synthesised("Porto is lovely in June.");
    await tick();
    expect(h.asked).toEqual(["Porto is lovely in June.", "Book the train early."]);
    h.synthesised("Book the train early.");
    await tick();
    h.ended("Porto is lovely in June.");
    await tick();
    h.ended("Book the train early.");
    await done;
    expect(h.played).toEqual(["Porto is lovely in June.", "Book the train early."]);
    expect(activityStore.voiced()).toBe(false);
  });

  it("a reply cut short says only its new ending", async () => {
    const h = harness();
    h.queue.feed("Once upon a time there was a fox. It lived ");
    void h.queue.finish(
      "Once upon a time there was a fox. It lived (I stopped early: I was asked to stop.)",
    );
    await tick();
    h.synthesised("Once upon a time there was a fox.");
    await tick();
    expect(h.asked.at(-1)).toBe("It lived (I stopped early: I was asked to stop.)");
    h.queue.stop();
  });

  it("stop ends the sentence playing, the queued ones and the requests in flight", async () => {
    const h = harness();
    h.queue.feed("Porto is lovely in June. The train is the better choice. ");
    await tick();
    h.synthesised("Porto is lovely in June.");
    await tick();
    h.queue.stop();
    h.synthesised("The train is the better choice.");
    await tick();
    expect(h.stopped).toEqual(["Porto is lovely in June."]);
    expect(h.played).toEqual(["Porto is lovely in June."]);
    expect(h.signals.every((signal) => signal.aborted)).toBe(true);
    expect(activityStore.voiced()).toBe(false);
  });
});
