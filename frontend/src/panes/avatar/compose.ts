/**
 * Smoothed intent → VRM expression weights, through a character profile (docs/15 §7).
 *
 * Pure. Every number the face shows comes out of this function, so "why does it look like
 * that?" is answerable from the intent and the profile alone.
 */

import type { Overlay } from "../../transport/types";

import type { CharacterProfile, ExpressionWeights } from "./profile";

/** The intent after per-channel smoothing — what the face is moving through right now. */
export interface FaceState {
  readonly valence: number;
  readonly arousal: number;
  readonly overlay: Readonly<Record<Overlay, number>>;
}

/** Expressions driven by idle motion, not by emotion; kept out of the emotional blend cap. */
const IDLE_EXPRESSIONS = new Set(["blink"]);

export function expressionWeights(face: FaceState, profile: CharacterProfile): ExpressionWeights {
  const weights: ExpressionWeights = {};
  const add = (map: ExpressionWeights, scale: number) => {
    if (scale <= 0) return;
    for (const [name, weight] of Object.entries(map)) {
      weights[name] = (weights[name] ?? 0) + weight * scale;
    }
  };

  for (const [overlay, weight] of Object.entries(face.overlay) as [Overlay, number][]) {
    const map = profile.overlay_map[overlay];
    if (map) add(map, weight);
  }
  add(profile.mood_map.valence_positive, Math.max(0, face.valence));
  add(profile.mood_map.valence_negative, Math.max(0, -face.valence));
  add(profile.mood_map.arousal, face.arousal);

  // Cap the emotional blend as a whole, proportionally, so a strong mood never stacks
  // expressions into a distorted face (docs/15 §10, "overlay conflict").
  const emotional = Object.entries(weights).filter(([name]) => !IDLE_EXPRESSIONS.has(name));
  const total = emotional.reduce((sum, [, weight]) => sum + weight, 0);
  const scale = total > profile.limits.max_total_blend ? profile.limits.max_total_blend / total : 1;
  for (const [name, weight] of emotional) weights[name] = weight * scale;

  for (const name of Object.keys(weights)) weights[name] = clamp01(weights[name] ?? 0);
  return weights;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}
