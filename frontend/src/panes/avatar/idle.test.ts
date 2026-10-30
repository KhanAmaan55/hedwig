import { describe, expect, it } from "vitest";

import { IDLE, IdleScheduler } from "./idle";
import { seeded } from "./testing";

const CALM = { arousal: 0.3, energy: 0.5, reducedMotion: false };

function simulate(seconds: number, context = CALM, seed = 7): IdleScheduler {
  const idle = new IdleScheduler(seeded(seed));
  for (let t = 0; t < seconds; t += 1 / 60) idle.step(1 / 60, context);
  return idle;
}

describe("IdleScheduler", () => {
  it("blinks about every four seconds over a simulated hour (docs/15 §5.1, §11)", () => {
    const idle = simulate(3600);
    const meanGap = 3600 / idle.blinks;
    expect(meanGap).toBeGreaterThan(IDLE.blinkMeanS * 0.85);
    expect(meanGap).toBeLessThan(IDLE.blinkMeanS * 1.2);
  });

  it("moves its eyes about every three seconds", () => {
    const idle = simulate(3600);
    const meanGap = 3600 / idle.saccades;
    expect(meanGap).toBeGreaterThan(IDLE.saccadeMeanS * 0.85);
    expect(meanGap).toBeLessThan(IDLE.saccadeMeanS * 1.2);
  });

  it("never blinks twice in quick succession — a double blink reads as a twitch", () => {
    const idle = new IdleScheduler(seeded(3));
    let last = -Infinity;
    let blinks = 0;
    for (let t = 0; t < 600; t += 1 / 60) {
      idle.step(1 / 60, CALM);
      if (idle.blinks > blinks) {
        expect(t - last).toBeGreaterThanOrEqual(IDLE.blinkMinGapS - 1 / 60);
        last = t;
        blinks = idle.blinks;
      }
    }
  });

  it("closes the eyes fully mid-blink and opens them again", () => {
    const idle = new IdleScheduler(seeded(11));
    let peak = 0;
    let frame = { blink: 0 };
    for (let t = 0; t < 30; t += 1 / 120) {
      frame = idle.step(1 / 120, CALM);
      peak = Math.max(peak, frame.blink);
    }
    expect(peak).toBeGreaterThan(0.95);
    expect(frame.blink).toBeLessThanOrEqual(1);
  });

  it("looks further afield when aroused", () => {
    const reach = (arousal: number) => {
      const idle = new IdleScheduler(seeded(5));
      let max = 0;
      for (let t = 0; t < 600; t += 1 / 30) {
        const { gaze } = idle.step(1 / 30, { ...CALM, arousal });
        max = Math.max(max, Math.hypot(gaze.x, gaze.y));
      }
      return max;
    };
    expect(reach(0.9)).toBeGreaterThan(reach(0.1));
  });

  it("breathes faster when aroused", () => {
    expect(IDLE.breathPeriodS(1)).toBeLessThan(IDLE.breathPeriodS(0));
  });

  it("moves the head less when tired", () => {
    const span = (energy: number) => {
      const idle = new IdleScheduler(seeded(9));
      let max = 0;
      for (let t = 0; t < 300; t += 1 / 30) {
        max = Math.max(max, Math.abs(idle.step(1 / 30, { ...CALM, energy }).head.yaw));
      }
      return max;
    };
    expect(span(0.1)).toBeLessThan(span(0.9));
  });

  it("stays still under reduced motion, but still blinks", () => {
    const idle = new IdleScheduler(seeded(2));
    let moved = 0;
    for (let t = 0; t < 120; t += 1 / 30) {
      const frame = idle.step(1 / 30, { ...CALM, reducedMotion: true });
      moved += Math.abs(frame.gaze.x) + Math.abs(frame.head.yaw) + Math.abs(frame.breath);
    }
    expect(moved).toBe(0);
    expect(idle.blinks).toBeGreaterThan(10);
  });

  it("is reproducible from a seed", () => {
    expect(simulate(60, CALM, 42).blinks).toBe(simulate(60, CALM, 42).blinks);
  });
});
