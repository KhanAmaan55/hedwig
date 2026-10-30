/**
 * Character profiles (docs/15 §7).
 *
 * Data, not code: a new character is a JSON file plus a model. The renderer never decides what
 * "warmth" looks like — the profile does — which is what keeps the expression vocabulary from
 * being shaped by whichever renderer happened to come first (ADR-0021).
 */

import type { Overlay } from "../../transport/types";

import defaultProfile from "./profiles/hedwig-default.json";

/** VRM expression name → weight 0..1. Preset names from the VRM 1.0 specification. */
export type ExpressionWeights = Record<string, number>;

export interface CharacterProfile {
  readonly name: string;
  readonly kind: "vrm";
  /** `| undefined` because a hand-edited JSON profile can say `null` or omit a key, and
   * `validateProfile` must report that rather than crash on it. */
  readonly overlay_map: Partial<Record<Overlay, ExpressionWeights | undefined>>;
  readonly mood_map: {
    readonly valence_positive: ExpressionWeights;
    readonly valence_negative: ExpressionWeights;
    readonly arousal: ExpressionWeights;
  };
  /** Overlays this character cannot express. Declared, so a gap is a decision, not an oversight. */
  readonly unsupported: readonly Overlay[];
  readonly limits: {
    /** Emotional expressions are scaled down together if their weights exceed this. */
    readonly max_total_blend: number;
    /** Radians at full idle amplitude. */
    readonly head_yaw: number;
    readonly head_pitch: number;
    readonly head_roll: number;
    readonly breath: number;
    /** How far the eyes may look, in the look-at plane, at full saccade. */
    readonly gaze: number;
  };
  /** Normalised bone rotations (radians, XYZ) applied once to bring the model out of its T-pose.
   * Typed as arrays, not tuples, because that is all JSON can promise; `validateProfile` checks
   * each has exactly three angles. */
  readonly rest_pose: Readonly<Record<string, readonly number[]>>;
}

export const OVERLAYS: readonly Overlay[] = [
  "interest",
  "warmth",
  "amusement",
  "uncertainty",
  "fatigue",
];

/** The shape TypeScript cannot check from JSON is checked by `validateProfile` — in tests, and
 * every time a model loads. */
export const DEFAULT_PROFILE: CharacterProfile = defaultProfile as CharacterProfile;

/**
 * Every overlay in the vocabulary must be mapped or declared unsupported (docs/15 §11).
 * Returns the problems; empty means valid.
 */
export function validateProfile(profile: CharacterProfile): string[] {
  const problems: string[] = [];
  for (const overlay of OVERLAYS) {
    const mapped = profile.overlay_map[overlay] !== undefined;
    const declared = profile.unsupported.includes(overlay);
    if (!mapped && !declared)
      problems.push(`overlay "${overlay}" is neither mapped nor declared unsupported`);
    if (mapped && declared)
      problems.push(`overlay "${overlay}" is both mapped and declared unsupported`);
  }
  for (const [overlay, weights] of Object.entries(profile.overlay_map)) {
    if (!weights) continue; // reported above as unmapped
    for (const [name, weight] of Object.entries(weights)) {
      if (weight < 0 || weight > 1)
        problems.push(`${overlay} → ${name}: weight ${weight} outside 0..1`);
    }
  }
  if (profile.limits.max_total_blend <= 0) problems.push("max_total_blend must be positive");
  for (const [bone, rotation] of Object.entries(profile.rest_pose)) {
    if (rotation.length !== 3 || rotation.some((angle) => !Number.isFinite(angle))) {
      problems.push(`rest_pose.${bone} must be three finite angles`);
    }
  }
  return problems;
}
