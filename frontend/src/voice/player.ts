/**
 * Saying a reply out loud, and moving the mouth while it is said (docs/15 §13).
 *
 * Two ways to speak, one interface: with Piper's audio, the mouth follows the audio's live
 * amplitude (AVA-02); without it, a text-paced movement stands in, so the face still speaks.
 * Either can be stopped at once (part of VOI-03).
 */

import { activityStore } from "../stores/activity";

import { visemeAt } from "./visemes";

export interface Utterance {
  /** Resolves when speaking ends, naturally or by `stop`. */
  readonly done: Promise<void>;
  readonly stop: () => void;
}

/** Characters a second for the text-paced mouth, and its cap. */
const PACE = 12;
const MAX_PACED_MS = 6000;

export function speakAudio(
  audio: ArrayBuffer,
  context: AudioContext = new AudioContext(),
  text = "",
): Utterance {
  const analyser = context.createAnalyser();
  analyser.fftSize = 512;
  const samples = new Float32Array(analyser.fftSize);
  let source: AudioBufferSourceNode | null = null;
  let frame = 0;
  let started = 0;
  let duration = 1;
  let finish: () => void = () => undefined;
  const done = new Promise<void>((resolve) => {
    finish = resolve;
  });

  const end = () => {
    cancelAnimationFrame(frame);
    activityStore.setMouth(0);
    activityStore.set("idle");
    void context.close().catch(() => undefined);
    finish();
  };

  const level = () => {
    analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (const sample of samples) sum += sample * sample;
    // RMS of speech sits around 0.05-0.2; scaled so ordinary speech opens the mouth well.
    activityStore.setMouth(Math.min(1, Math.sqrt(sum / samples.length) * 6));
    if (text) activityStore.setShape(visemeAt(text, (context.currentTime - started) / duration));
    frame = requestAnimationFrame(level);
  };

  void context
    .decodeAudioData(audio.slice(0))
    .then((buffer) => {
      source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(analyser);
      analyser.connect(context.destination);
      source.onended = end;
      activityStore.set("speaking");
      duration = Math.max(0.1, buffer.duration);
      started = context.currentTime;
      source.start();
      frame = requestAnimationFrame(level);
    })
    .catch(end);

  return {
    done,
    stop() {
      if (source) {
        source.onended = null;
        source.stop();
      }
      end();
    },
  };
}

/** No audio: move the mouth at roughly the pace the text would be read, then stop. */
export function speakSilently(text: string): Utterance {
  const duration = Math.min(MAX_PACED_MS, (text.length / PACE) * 1000);
  const started = performance.now();
  let finish: () => void = () => undefined;
  const done = new Promise<void>((resolve) => {
    finish = resolve;
  });
  activityStore.set("speaking");
  const timer = window.setInterval(() => {
    const t = (performance.now() - started) / 1000;
    if (t * 1000 >= duration) {
      stop();
      return;
    }
    // Two overlapping rhythms read as syllables rather than a metronome.
    activityStore.setMouth(0.35 + 0.3 * Math.abs(Math.sin(t * 9)) + 0.15 * Math.sin(t * 3.7));
    activityStore.setShape(visemeAt(text, (t * 1000) / duration));
  }, 50);

  function stop() {
    window.clearInterval(timer);
    activityStore.setMouth(0);
    activityStore.set("idle");
    finish();
  }
  return { done, stop };
}
