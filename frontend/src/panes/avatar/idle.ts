/**
 * Idle behaviour: blink, saccade, breath, head drift (docs/15 §5.1).
 *
 * Stochastic by nature, so kept apart from resolution, which must stay pure. The randomness is
 * injected, which is what lets a test simulate an hour and check the rates match their
 * distributions.
 *
 * Generated here in the client rather than streamed from the backend (docs/28 §3): blinks and
 * saccades described at 10 Hz over a network would be bandwidth spent on noise; generated
 * locally they cost nothing and are smooth at display rate.
 */

export interface IdleContext {
  /** 0..1 — scales saccade amplitude and breathing rate. */
  readonly arousal: number;
  /** 0..1 — scales how much the head moves. A tired face moves less. */
  readonly energy: number;
  /** `prefers-reduced-motion`: no saccades, head drift or breathing. Blinks remain. */
  readonly reducedMotion: boolean;
}

/** One frame of idle motion. Units are normalised; the character profile turns them into angles. */
export interface IdleFrame {
  /** 0 open … 1 closed. */
  readonly blink: number;
  /** −1..1 each: where the eyes are pointed, relative to the user. */
  readonly gaze: { readonly x: number; readonly y: number };
  /** −1..1: the breathing cycle. */
  readonly breath: number;
  /** −1..1 each. */
  readonly head: { readonly yaw: number; readonly pitch: number; readonly roll: number };
}

export const IDLE = {
  blinkMeanS: 4,
  blinkDurationS: 0.15,
  /** No two blinks closer than this: a double blink reads as a twitch. */
  blinkMinGapS: 0.8,
  saccadeMeanS: 3,
  saccadeMinGapS: 0.4,
  breathPeriodS: (arousal: number) => 4.5 - 1.5 * arousal,
} as const;

export type Random = () => number;

export class IdleScheduler {
  private time = 0;
  private nextBlink: number;
  private blinkStart = Number.NEGATIVE_INFINITY;
  private nextSaccade: number;
  private gaze = { x: 0, y: 0 };
  private breathPhase = 0;

  /** Counters, for statistics tests and diagnostics. */
  blinks = 0;
  saccades = 0;

  constructor(private readonly random: Random = Math.random) {
    this.nextBlink = this.interval(IDLE.blinkMeanS, IDLE.blinkMinGapS);
    this.nextSaccade = this.interval(IDLE.saccadeMeanS, IDLE.saccadeMinGapS);
  }

  step(dt: number, context: IdleContext): IdleFrame {
    this.time += Math.max(0, dt);

    // -- blink: a Poisson process, rendered as a quick close-and-open ---------
    if (this.time >= this.nextBlink) {
      this.blinkStart = this.time;
      this.blinks += 1;
      this.nextBlink = this.time + this.interval(IDLE.blinkMeanS, IDLE.blinkMinGapS);
    }
    const blinkProgress = (this.time - this.blinkStart) / IDLE.blinkDurationS;
    const blink = blinkProgress >= 0 && blinkProgress <= 1 ? Math.sin(Math.PI * blinkProgress) : 0;

    if (context.reducedMotion) {
      return { blink, gaze: { x: 0, y: 0 }, breath: 0, head: { yaw: 0, pitch: 0, roll: 0 } };
    }

    // -- saccade: a new fixation point now and then, wider when aroused --------
    if (this.time >= this.nextSaccade) {
      const reach = 0.25 + 0.5 * clamp01(context.arousal);
      const angle = this.random() * Math.PI * 2;
      const distance = Math.sqrt(this.random()) * reach;
      this.gaze = { x: Math.cos(angle) * distance, y: Math.sin(angle) * distance * 0.6 };
      this.saccades += 1;
      this.nextSaccade = this.time + this.interval(IDLE.saccadeMeanS, IDLE.saccadeMinGapS);
    }

    // -- breath: a sinusoid, quicker when aroused ------------------------------
    this.breathPhase += dt / IDLE.breathPeriodS(clamp01(context.arousal));
    const breath = Math.sin(2 * Math.PI * this.breathPhase);

    // -- head drift: incommensurate sines, so the motion never visibly repeats --
    const amplitude = 0.35 + 0.65 * clamp01(context.energy);
    const t = this.time;
    const head = {
      yaw: amplitude * (0.6 * Math.sin(t * 0.31) + 0.4 * Math.sin(t * 0.17 + 1.3)),
      pitch: amplitude * (0.6 * Math.sin(t * 0.23 + 0.7) + 0.4 * Math.sin(t * 0.11 + 2.1)),
      roll: amplitude * (0.7 * Math.sin(t * 0.19 + 2.4) + 0.3 * Math.sin(t * 0.07)),
    };

    return { blink, gaze: { ...this.gaze }, breath, head };
  }

  /** Exponentially distributed: the waiting time of a Poisson process. */
  private interval(mean: number, minimum: number): number {
    const u = Math.min(this.random(), 1 - Number.EPSILON);
    return Math.max(minimum, -Math.log(1 - u) * mean);
  }
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}
