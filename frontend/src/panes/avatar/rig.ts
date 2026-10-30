/**
 * What the animator does to a body, behind a narrow interface.
 *
 * The VRM adapter in `VrmAvatar.tsx` implements this with three-vrm; tests implement it with a
 * recording fake. That split is what lets the whole animation pipeline — springs, idle motion,
 * composition, limits — be tested without WebGL, which jsdom does not have.
 */

export interface RigTarget {
  /** Set a VRM expression (preset name) to a weight 0..1. Unknown names are ignored. */
  setExpression(name: string, weight: number): void;
  /** Head rotation in radians. Already clamped to the profile's limits. */
  setHead(yaw: number, pitch: number, roll: number): void;
  /** Chest rotation in radians for the breathing cycle. */
  setBreath(angle: number): void;
  /** Where the eyes look, as an offset in the look-at plane in front of the face. */
  setGaze(x: number, y: number): void;
}

export function clamp(value: number, limit: number): number {
  return Math.max(-limit, Math.min(limit, value));
}
