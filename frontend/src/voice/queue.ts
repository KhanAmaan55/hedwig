/**
 * Speaking a reply sentence by sentence as it streams (docs/15 §14).
 *
 * Each complete sentence is synthesised as soon as the one before it has been — one at a time,
 * ahead of playback — and played strictly in order. The final reply settles whatever is left. `stop` ends the sentence playing, the ones queued and
 * the requests in flight.
 */

import { activityStore } from "../stores/activity";

import type { Utterance } from "./player";
import { takeSentences } from "./sentences";

export type Synthesise = (text: string, signal: AbortSignal) => Promise<ArrayBuffer>;
export type Play = (audio: ArrayBuffer, text: string) => Utterance;

export class SentenceQueue {
  private buffer = "";
  private sent = "";
  private chain: Promise<void> = Promise.resolve();
  /** One synthesis at a time: three at once made each slower than all three in a row. */
  private synthesis: Promise<unknown> = Promise.resolve();
  private current: Utterance | null = null;
  private stopped = false;
  private finished = false;
  private readonly controller = new AbortController();

  constructor(
    private readonly synthesise: Synthesise,
    private readonly play: Play,
  ) {
    activityStore.setVoiced(true);
  }

  /** More of the reply arrived. */
  feed(piece: string): void {
    if (this.stopped || this.finished) return;
    this.buffer += piece;
    const { sentences, rest } = takeSentences(this.buffer);
    this.buffer = rest;
    for (const sentence of sentences) this.enqueue(sentence);
  }

  /** The authoritative reply (ADR-0023): say what it has beyond what was already sent. */
  finish(reply: string): Promise<void> {
    if (!this.stopped && !this.finished) {
      this.finished = true;
      const said = squash(this.sent);
      const whole = squash(reply);
      const remainder = !said ? whole : whole.startsWith(said) ? whole.slice(said.length) : "";
      if (remainder.trim()) this.enqueue(remainder.trim());
    }
    this.chain = this.chain.then(() => {
      this.release();
    });
    return this.chain;
  }

  stop(): void {
    this.stopped = true;
    this.controller.abort();
    this.current?.stop();
    this.current = null;
    this.release();
  }

  private enqueue(text: string): void {
    this.sent += ` ${text}`;
    // Synthesised as soon as the one before it is (ahead of playback); played in turn.
    const audio = this.synthesis.then(() =>
      this.halted()
        ? Promise.reject(new Error("stopped"))
        : this.synthesise(text, this.controller.signal),
    );
    this.synthesis = audio.catch(() => undefined);
    audio.catch(() => undefined); // a failed sentence is skipped, below, not unhandled
    this.chain = this.chain.then(async () => {
      if (this.halted()) return;
      let buffer: ArrayBuffer;
      try {
        buffer = await audio;
      } catch {
        return;
      }
      if (this.halted()) return; // stopped while it was being synthesised
      let utterance: Utterance;
      try {
        utterance = this.play(buffer, text);
      } catch {
        return; // no audio device, say: the next sentence still gets its turn
      }
      this.current = utterance;
      await utterance.done;
      if (this.current === utterance) this.current = null;
    });
  }

  private halted(): boolean {
    return this.stopped;
  }

  private release(): void {
    if (!activityStore.voiced()) return;
    activityStore.setVoiced(false);
    activityStore.setMouth(0);
    if (activityStore.get() === "speaking") activityStore.set("idle");
  }
}

function squash(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}
