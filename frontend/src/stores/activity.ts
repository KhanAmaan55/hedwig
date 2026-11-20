/**
 * What HEDWIG is doing right now, for the face (docs/15 §13, docs/18 §4).
 *
 * Shared between the conversation pane (which knows a reply is coming, and when it is being
 * spoken) and the avatar (which shows it). Panes never import each other; both import this.
 *
 * Two speeds: `activity` changes a few times per turn and is a subscribable value; `mouth` changes
 * every frame and is a plain read, so the render loop never goes through React state.
 */

import { useSyncExternalStore } from "react";

export type Activity = "idle" | "thinking" | "speaking";

let activity: Activity = "idle";
let mouth = 0;
const listeners = new Set<() => void>();

export const activityStore = {
  get(): Activity {
    return activity;
  },
  set(next: Activity): void {
    if (next === activity) return;
    activity = next;
    if (next !== "speaking") mouth = 0;
    for (const listener of listeners) listener();
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  /** 0..1, how open the mouth is this frame. Written by the voice player or the text pacer. */
  mouth(): number {
    return activity === "speaking" ? mouth : 0;
  },
  setMouth(level: number): void {
    mouth = Math.max(0, Math.min(1, level));
  },
};

export function useActivity(): Activity {
  return useSyncExternalStore(
    (listener) => activityStore.subscribe(listener),
    () => activityStore.get(),
    () => activityStore.get(),
  );
}
