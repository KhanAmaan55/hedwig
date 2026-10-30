/**
 * Where the avatar comes from, and whether this display can draw it.
 *
 * Separate from the pane so the pane module exports components only (fast refresh), and so these
 * can be tested on their own.
 */

/** Relative, not `/avatars/…`: the packaged app loads from `file://`, where a root path resolves to
 * the filesystem root (vite.config.ts `base: "./"`). Gitignored: no model is committed (docs/28 §5.1). */
export const DEFAULT_MODEL = import.meta.env.VITE_HEDWIG_AVATAR ?? "avatars/hedwig.vrm";

export function hasWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") ?? canvas.getContext("webgl"));
  } catch {
    return false;
  }
}
