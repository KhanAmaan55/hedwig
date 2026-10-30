/**
 * Critically damped smoothing, one channel at a time (docs/15 §5).
 *
 * Emotion publishes on a 30-second tick, so a renderer that jumped straight to each new value
 * would visibly jolt. A spring per channel moves toward the target at that channel's own speed —
 * eyes fast, mood slow — which is most of what separates a face that feels alive from one that
 * feels mechanical.
 *
 * The exact critically damped solution (Game Programming Gems 4, "Critically Damped Ease-In/Out
 * Smoothing"), not Euler integration: it is stable at any frame time, so a dropped frame or a tab
 * returning from the background cannot make it oscillate or explode.
 */

/** docs/15 §5: how long each kind of channel takes to settle, in seconds. */
export const TIME_CONSTANTS = {
  mood: 1.2,
  overlay: 0.4,
  energy: 2.0,
  gaze: 0.15,
} as const;

export class Spring {
  private velocity = 0;

  constructor(
    public value: number,
    private readonly smoothTime: number,
  ) {}

  /** Advance toward `target` by `dt` seconds. Never overshoots. */
  step(target: number, dt: number): number {
    if (dt <= 0) return this.value;
    if (this.smoothTime <= 0) {
      this.value = target;
      this.velocity = 0;
      return this.value;
    }

    const omega = 2 / this.smoothTime;
    const x = omega * dt;
    const decay = 1 / (1 + x + 0.48 * x * x + 0.235 * x * x * x);
    const change = this.value - target;
    const temp = (this.velocity + omega * change) * dt;

    this.velocity = (this.velocity - omega * temp) * decay;
    let next = target + (change + temp) * decay;

    // Clamp: a critically damped spring should not cross its target, and the polynomial
    // approximation of exp() can push it a hair past at large dt.
    if (target - this.value > 0 === next > target) {
      next = target;
      this.velocity = 0;
    }
    this.value = next;
    return next;
  }

  /** Jump straight to `value` — for reduced motion, where changes should be instant. */
  snap(value: number): number {
    this.value = value;
    this.velocity = 0;
    return value;
  }
}
