import { describe, expect, it } from "vitest";

import type { Overlay } from "../../transport/types";

import { expressionWeights, type FaceState } from "./compose";
import { DEFAULT_PROFILE, OVERLAYS, validateProfile, type CharacterProfile } from "./profile";

const NONE: Record<Overlay, number> = {
  interest: 0,
  warmth: 0,
  amusement: 0,
  uncertainty: 0,
  fatigue: 0,
};

function face(
  overrides: Omit<Partial<FaceState>, "overlay"> & { overlay?: Partial<Record<Overlay, number>> },
): FaceState {
  return {
    valence: overrides.valence ?? 0,
    arousal: overrides.arousal ?? 0,
    overlay: { ...NONE, ...overrides.overlay },
  };
}

describe("the default character profile", () => {
  it("maps or declares every overlay in the vocabulary (docs/15 §11)", () => {
    expect(validateProfile(DEFAULT_PROFILE)).toEqual([]);
  });

  it("catches an overlay nobody mapped", () => {
    const broken = {
      ...DEFAULT_PROFILE,
      overlay_map: { ...DEFAULT_PROFILE.overlay_map, fatigue: undefined },
    } as unknown as CharacterProfile;
    expect(validateProfile(broken).join()).toContain("fatigue");
  });

  it("catches a rest pose that is not three angles", () => {
    const broken = { ...DEFAULT_PROFILE, rest_pose: { leftUpperArm: [0, 1] } };
    expect(validateProfile(broken).join()).toContain("leftUpperArm");
  });

  it("uses only VRM 1.0 preset expression names, so any VRM can show it", () => {
    const presets = new Set(["happy", "angry", "sad", "relaxed", "surprised", "blink"]);
    const names = [
      ...Object.values(DEFAULT_PROFILE.overlay_map).flatMap((map) => Object.keys(map ?? {})),
      ...Object.values(DEFAULT_PROFILE.mood_map).flatMap((map) => Object.keys(map)),
    ];
    for (const name of names) expect(presets).toContain(name);
  });

  it("never reaches for anger — the vocabulary has nothing to drive it", () => {
    for (const overlay of OVERLAYS) {
      expect(Object.keys(DEFAULT_PROFILE.overlay_map[overlay] ?? {})).not.toContain("angry");
    }
  });
});

describe("expressionWeights", () => {
  it("a neutral face is neutral", () => {
    const weights = expressionWeights(face({}), DEFAULT_PROFILE);
    expect(Object.values(weights).every((weight) => weight === 0)).toBe(true);
  });

  it("amusement shows as a smile", () => {
    expect(
      expressionWeights(face({ overlay: { amusement: 1 } }), DEFAULT_PROFILE).happy,
    ).toBeGreaterThan(0.5);
  });

  it("positive and negative mood pull in opposite directions", () => {
    const up = expressionWeights(face({ valence: 0.8 }), DEFAULT_PROFILE);
    const down = expressionWeights(face({ valence: -0.8 }), DEFAULT_PROFILE);
    expect(up.happy ?? 0).toBeGreaterThan(down.happy ?? 0);
    expect(down.sad ?? 0).toBeGreaterThan(up.sad ?? 0);
  });

  it("caps the emotional blend so expressions never stack into a distorted face", () => {
    const everything = face({
      valence: 1,
      arousal: 1,
      overlay: { interest: 1, warmth: 1, amusement: 1, uncertainty: 1, fatigue: 1 },
    });
    const weights = expressionWeights(everything, DEFAULT_PROFILE);
    const emotional = Object.entries(weights)
      .filter(([name]) => name !== "blink")
      .reduce((sum, [, weight]) => sum + weight, 0);
    expect(emotional).toBeLessThanOrEqual(DEFAULT_PROFILE.limits.max_total_blend + 1e-9);
  });

  it("keeps the balance of an expression when it caps it", () => {
    const weights = expressionWeights(
      face({ overlay: { amusement: 1, interest: 1, warmth: 1 } }),
      DEFAULT_PROFILE,
    );
    expect(weights.happy ?? 0).toBeGreaterThan(weights.surprised ?? 0);
  });

  it("fatigue lowers the eyelids, outside the emotional cap", () => {
    const tired = expressionWeights(face({ overlay: { fatigue: 1 } }), DEFAULT_PROFILE);
    expect(tired.blink).toBeGreaterThan(0);
  });

  it("keeps every weight in 0..1", () => {
    for (const valence of [-1, 0, 1]) {
      for (const level of [0, 0.5, 1]) {
        const weights = expressionWeights(
          face({ valence, arousal: level, overlay: { interest: level, uncertainty: level } }),
          DEFAULT_PROFILE,
        );
        for (const weight of Object.values(weights)) {
          expect(weight).toBeGreaterThanOrEqual(0);
          expect(weight).toBeLessThanOrEqual(1);
        }
      }
    }
  });
});
