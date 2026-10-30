import { describe, expect, it } from "vitest";

import type { ExpressionIntent } from "../../transport/types";

import { AvatarAnimator, RESTING_INTENT } from "./animator";
import { DEFAULT_PROFILE } from "./profile";
import { RecordingRig, seeded } from "./testing";

const DT = 1 / 60;

function run(animator: AvatarAnimator, rig: RecordingRig, seconds: number): void {
  for (let t = 0; t < seconds; t += DT) animator.step(DT, rig);
}

function intent(overrides: Partial<ExpressionIntent>): ExpressionIntent {
  return { ...RESTING_INTENT, ...overrides };
}

describe("AvatarAnimator", () => {
  it("reacts to emotion: an amused intent becomes a smile", () => {
    const animator = new AvatarAnimator(DEFAULT_PROFILE, false, seeded(1));
    const rig = new RecordingRig();
    run(animator, rig, 1);
    const before = rig.expressions.get("happy") ?? 0;

    animator.setIntent(
      intent({ mood: { valence: 0.7, arousal: 0.5 }, overlay: { amusement: 0.8 } }),
    );
    run(animator, rig, 3);

    expect(rig.expressions.get("happy") ?? 0).toBeGreaterThan(before + 0.3);
  });

  it("eases into a new expression rather than jumping (docs/15 §5)", () => {
    const animator = new AvatarAnimator(DEFAULT_PROFILE, false, seeded(1));
    const rig = new RecordingRig();
    animator.setIntent(intent({ overlay: { amusement: 1 } }));
    animator.step(DT, rig);
    const firstFrame = rig.expressions.get("happy") ?? 0;
    run(animator, rig, 2);

    expect(firstFrame).toBeLessThan(0.2);
    expect(rig.expressions.get("happy") ?? 0).toBeGreaterThan(firstFrame);
  });

  it("changes instantly under reduced motion", () => {
    const animator = new AvatarAnimator(DEFAULT_PROFILE, true, seeded(1));
    const rig = new RecordingRig();
    animator.setIntent(intent({ overlay: { amusement: 1 } }));
    animator.step(DT, rig);

    expect(rig.expressions.get("happy") ?? 0).toBeGreaterThan(0.5);
  });

  it("returns a faded expression to zero rather than leaving it set", () => {
    const animator = new AvatarAnimator(DEFAULT_PROFILE, true, seeded(1));
    const rig = new RecordingRig();
    animator.setIntent(intent({ overlay: { uncertainty: 1 } }));
    animator.step(DT, rig);
    expect(rig.expressions.get("sad") ?? 0).toBeGreaterThan(0);

    animator.setIntent(RESTING_INTENT);
    animator.step(DT, rig);
    expect(rig.expressions.get("sad")).toBe(0);
  });

  it("blinks on its own, about every four seconds", () => {
    const animator = new AvatarAnimator(DEFAULT_PROFILE, false, seeded(4));
    const rig = new RecordingRig();
    run(animator, rig, 120);
    expect(animator.stats.blinks).toBeGreaterThan(20);
    expect(animator.stats.blinks).toBeLessThan(45);
  });

  it("moves eyes and head, but never past the profile's limits", () => {
    const animator = new AvatarAnimator(DEFAULT_PROFILE, false, seeded(8));
    const rig = new RecordingRig();
    animator.setIntent(intent({ mood: { valence: 0.5, arousal: 1 }, energy: 1 }));
    run(animator, rig, 300);
    const { limits } = DEFAULT_PROFILE;

    expect(rig.maxAbs.gaze).toBeGreaterThan(0);
    expect(rig.maxAbs.yaw).toBeGreaterThan(0);
    expect(rig.maxAbs.gaze).toBeLessThanOrEqual(limits.gaze);
    expect(rig.maxAbs.yaw).toBeLessThanOrEqual(limits.head_yaw);
    expect(rig.maxAbs.pitch).toBeLessThanOrEqual(limits.head_pitch);
    expect(rig.maxAbs.roll).toBeLessThanOrEqual(limits.head_roll);
    expect(rig.maxAbs.breath).toBeLessThanOrEqual(limits.breath);
  });

  it("a tired face holds its eyelids partly down between blinks", () => {
    const animator = new AvatarAnimator(DEFAULT_PROFILE, true, seeded(1));
    const rig = new RecordingRig();
    animator.setIntent(intent({ overlay: { fatigue: 1 }, energy: 0.1 }));
    animator.step(DT, rig);
    expect(rig.expressions.get("blink") ?? 0).toBeGreaterThan(0.2);
  });

  it("keeps idling when no intent ever arrives", () => {
    const animator = new AvatarAnimator(DEFAULT_PROFILE, false, seeded(2));
    const rig = new RecordingRig();
    run(animator, rig, 30);
    expect(animator.stats.blinks).toBeGreaterThan(0);
    expect(animator.intent).toBe(RESTING_INTENT);
  });
});
