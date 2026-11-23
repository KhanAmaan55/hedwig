/**
 * Mouth shapes from the words being said (AVA-03, docs/15 §13).
 *
 * Piper's command line gives audio but no phoneme timings, and reaching into its Python API would
 * pull GPL code into HEDWIG's process. So the shape comes from the text: the playhead's position in
 * the audio picks a position in the words, and the vowel there picks one of the five VRM mouth
 * shapes. Loudness still decides how *open* the mouth is (AVA-02); this decides its *shape*.
 * Letter-based, not phonetic — "though" is not "tow" — but a mouth that rounds on "you" and
 * widens on "see" reads as speech in a way a single open-close never did.
 */

export type Viseme = "aa" | "ee" | "ih" | "oh" | "ou";

const DIGRAPHS: [string, Viseme][] = [
  ["oo", "ou"],
  ["ou", "ou"],
  ["ew", "ou"],
  ["ue", "ou"],
  ["ee", "ee"],
  ["ea", "ee"],
  ["ie", "ee"],
  ["ey", "ee"],
  ["ai", "ee"],
  ["ay", "ee"],
  ["oa", "oh"],
  ["ow", "oh"],
  ["au", "aa"],
  ["aw", "aa"],
];
const SINGLE: Record<string, Viseme> = { a: "aa", e: "ee", i: "ih", y: "ih", o: "oh", u: "ou" };

/** The shape at `fraction` (0..1) of the way through `text`: the nearest vowel at or before it. */
export function visemeAt(text: string, fraction: number): Viseme {
  const lower = text.toLowerCase();
  const at = Math.max(0, Math.min(lower.length - 1, Math.floor(fraction * lower.length)));
  for (let index = at; index >= 0; index -= 1) {
    // A pair ending here ("se|a" in "sea") or starting here: the letter belongs to it.
    for (const pair of [
      lower.slice(Math.max(0, index - 1), index + 1),
      lower.slice(index, index + 2),
    ]) {
      const digraph = DIGRAPHS.find(([letters]) => letters === pair);
      if (digraph) return digraph[1];
    }
    const single = SINGLE[lower.charAt(index)];
    if (single) return single;
  }
  return "aa";
}
