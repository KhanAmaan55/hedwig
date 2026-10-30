import { describe, expect, it } from "vitest";

import { Spring, TIME_CONSTANTS } from "./spring";

describe("Spring", () => {
  it("settles on its target", () => {
    const spring = new Spring(0, 0.4);
    for (let i = 0; i < 300; i += 1) spring.step(1, 1 / 60);
    expect(spring.value).toBeCloseTo(1, 4);
  });

  it("never overshoots — a face must not bounce past an expression (docs/15 §11)", () => {
    for (const [target, dt] of [
      [1, 1 / 60],
      [-0.7, 1 / 30],
      [0.5, 0.1],
    ] as const) {
      const spring = new Spring(0, 0.4);
      for (let i = 0; i < 400; i += 1) {
        const value = spring.step(target, dt);
        expect(Math.abs(value)).toBeLessThanOrEqual(Math.abs(target) + 1e-9);
      }
    }
  });

  it("moves monotonically toward a step change", () => {
    const spring = new Spring(0, 1.2);
    let previous = 0;
    for (let i = 0; i < 200; i += 1) {
      const value = spring.step(1, 1 / 60);
      expect(value).toBeGreaterThanOrEqual(previous);
      previous = value;
    }
  });

  it("is stable at a huge frame time, as after a tab wakes", () => {
    // Nearly there in one step, and — what matters — not past it and not oscillating.
    const spring = new Spring(0, 0.15);
    const value = spring.step(1, 5);
    expect(value).toBeGreaterThan(0.99);
    expect(value).toBeLessThanOrEqual(1);
    expect(spring.step(1, 5)).toBeLessThanOrEqual(1);
  });

  it("moves slow channels slower than fast ones: eyes fast, mood slow (docs/15 §5)", () => {
    const eyes = new Spring(0, TIME_CONSTANTS.gaze);
    const mood = new Spring(0, TIME_CONSTANTS.mood);
    for (let i = 0; i < 12; i += 1) {
      eyes.step(1, 1 / 60);
      mood.step(1, 1 / 60);
    }
    expect(eyes.value).toBeGreaterThan(mood.value);
  });

  it("snaps instantly for reduced motion", () => {
    const spring = new Spring(0, 1.2);
    expect(spring.snap(0.8)).toBe(0.8);
    expect(spring.step(0.8, 1 / 60)).toBe(0.8);
  });
});
