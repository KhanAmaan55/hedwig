/**
 * The animation pipeline, with no renderer in it (docs/28 §5).
 *
 *     intent ──► springs ──► compose(profile) ──┐
 *                                               ├──► RigTarget
 *     idle scheduler ──► springs (gaze) ────────┘
 *
 * `setIntent` is called when the backend's intent changes — every 30 s at most. `step` is called
 * every frame. Nothing here touches React state: the render loop reads and writes refs, because
 * sixty React updates a second is how a UI becomes unusable (docs/18 §4 rule 3).
 */

import type { ExpressionIntent, Overlay } from "../../transport/types";

import { expressionWeights } from "./compose";
import { IdleScheduler, type Random } from "./idle";
import { OVERLAYS, type CharacterProfile } from "./profile";
import { clamp, type RigTarget } from "./rig";
import { Spring, TIME_CONSTANTS } from "./spring";

/** What the face shows before the first intent arrives: calm, faintly attentive. */
export const RESTING_INTENT: ExpressionIntent = {
  mood: { valence: 0, arousal: 0.3 },
  overlay: {},
  gaze: "user",
  energy: 0.5,
  action: "idle",
  emotion_version: null,
};

export class AvatarAnimator {
  private readonly valence = new Spring(0, TIME_CONSTANTS.mood);
  private readonly arousal = new Spring(0.3, TIME_CONSTANTS.mood);
  private readonly energy = new Spring(0.5, TIME_CONSTANTS.energy);
  private readonly overlays = new Map<Overlay, Spring>(
    OVERLAYS.map((overlay) => [overlay, new Spring(0, TIME_CONSTANTS.overlay)]),
  );
  private readonly gazeX = new Spring(0, TIME_CONSTANTS.gaze);
  private readonly gazeY = new Spring(0, TIME_CONSTANTS.gaze);
  private readonly idle: IdleScheduler;
  private target: ExpressionIntent = RESTING_INTENT;
  private expressionNames = new Set<string>();

  constructor(
    private readonly profile: CharacterProfile,
    private reducedMotion = false,
    random?: Random,
  ) {
    this.idle = new IdleScheduler(random);
  }

  /** The emotion API: tell the face what to express. It gets there at its own pace. */
  setIntent(intent: ExpressionIntent): void {
    this.target = intent;
  }

  setReducedMotion(reduced: boolean): void {
    this.reducedMotion = reduced;
  }

  get intent(): ExpressionIntent {
    return this.target;
  }

  /** Advance one frame and apply it. `dt` in seconds. */
  step(dt: number, rig: RigTarget): void {
    const intent = this.target;
    const follow = (spring: Spring, value: number) =>
      // Reduced motion: expression changes become instant rather than animated (docs/18 §6).
      this.reducedMotion ? spring.snap(value) : spring.step(value, dt);

    const valence = follow(this.valence, intent.mood.valence);
    const arousal = follow(this.arousal, intent.mood.arousal);
    const energy = follow(this.energy, intent.energy);
    const overlay = {} as Record<Overlay, number>;
    for (const [name, spring] of this.overlays)
      overlay[name] = follow(spring, intent.overlay[name] ?? 0);

    const idle = this.idle.step(dt, { arousal, energy, reducedMotion: this.reducedMotion });

    // -- face --------------------------------------------------------------------
    const weights = expressionWeights({ valence, arousal, overlay }, this.profile);
    // An eyelid is closed by whichever is stronger: a blink, or fatigue holding it half down.
    weights.blink = Math.max(weights.blink ?? 0, idle.blink);

    // Expressions that faded to nothing must be set back to zero, not merely left out.
    for (const name of this.expressionNames) if (!(name in weights)) rig.setExpression(name, 0);
    for (const [name, weight] of Object.entries(weights)) rig.setExpression(name, weight);
    this.expressionNames = new Set(Object.keys(weights));

    // -- eyes, head, breath --------------------------------------------------------
    const { limits } = this.profile;
    rig.setGaze(
      clamp(follow(this.gazeX, idle.gaze.x) * limits.gaze, limits.gaze),
      clamp(follow(this.gazeY, idle.gaze.y) * limits.gaze, limits.gaze),
    );
    rig.setHead(
      clamp(idle.head.yaw * limits.head_yaw, limits.head_yaw),
      clamp(idle.head.pitch * limits.head_pitch, limits.head_pitch),
      clamp(idle.head.roll * limits.head_roll, limits.head_roll),
    );
    rig.setBreath(clamp(idle.breath * limits.breath, limits.breath));
  }

  /** For tests and diagnostics. */
  get stats(): { blinks: number; saccades: number } {
    return { blinks: this.idle.blinks, saccades: this.idle.saccades };
  }
}
