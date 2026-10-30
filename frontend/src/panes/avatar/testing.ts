/** Test helpers for the avatar pane. Not imported by application code. */

import type { RigTarget } from "./rig";

/** A seeded RNG (mulberry32), so statistical tests are reproducible. */
export function seeded(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A `RigTarget` that records the last value of everything, instead of moving a model. */
export class RecordingRig implements RigTarget {
  expressions = new Map<string, number>();
  head = { yaw: 0, pitch: 0, roll: 0 };
  breath = 0;
  gaze = { x: 0, y: 0 };
  maxAbs = { yaw: 0, pitch: 0, roll: 0, breath: 0, gaze: 0 };

  setExpression(name: string, weight: number): void {
    this.expressions.set(name, weight);
  }
  setHead(yaw: number, pitch: number, roll: number): void {
    this.head = { yaw, pitch, roll };
    this.maxAbs.yaw = Math.max(this.maxAbs.yaw, Math.abs(yaw));
    this.maxAbs.pitch = Math.max(this.maxAbs.pitch, Math.abs(pitch));
    this.maxAbs.roll = Math.max(this.maxAbs.roll, Math.abs(roll));
  }
  setBreath(angle: number): void {
    this.breath = angle;
    this.maxAbs.breath = Math.max(this.maxAbs.breath, Math.abs(angle));
  }
  setGaze(x: number, y: number): void {
    this.gaze = { x, y };
    this.maxAbs.gaze = Math.max(this.maxAbs.gaze, Math.abs(x), Math.abs(y));
  }
}
