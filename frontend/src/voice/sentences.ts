/**
 * Where a streamed reply's sentences end, so each can be spoken as soon as it is complete
 * (docs/15 §14). Deterministic and small on purpose: listed, so it is tested.
 */

/** Below this, a "sentence" waits for the next one: "Hi." is not worth a synthesis of its own. */
export const MIN_SENTENCE = 12;

const ABBREVIATIONS = new Set([
  "mr",
  "mrs",
  "ms",
  "dr",
  "st",
  "prof",
  "vs",
  "etc",
  "e.g",
  "i.e",
  "approx",
  "no",
]);

// A run of terminal punctuation, then any closing quotes or brackets, then whitespace.
const BOUNDARY = /([.!?…]+["'”’)\]]*)(\s+)|\n\s*\n/g;

/**
 * Split off the complete sentences at the front of `text`. `rest` is what is still being
 * written — it may be the start of the next sentence.
 */
export function takeSentences(text: string): { sentences: string[]; rest: string } {
  const sentences: string[] = [];
  let start = 0;
  let pending = "";
  BOUNDARY.lastIndex = 0;
  for (let match = BOUNDARY.exec(text); match !== null; match = BOUNDARY.exec(text)) {
    const end = match.index + (match[1]?.length ?? 0);
    const piece = text.slice(start, end);
    const words = piece.trim().split(/\s+/);
    const last = (words.at(-1) ?? "").replace(/[.!?…"'”’)\]]+$/, "").toLowerCase();
    if (match[1]?.startsWith(".") && ABBREVIATIONS.has(last)) continue;
    pending += piece;
    start = match.index + match[0].length;
    // A blank line is a pause of its own: say what came before it, however short.
    if (pending.trim().length >= MIN_SENTENCE || /\n\s*\n/.test(match[0])) {
      sentences.push(pending.trim());
      pending = "";
    } else {
      pending += " ";
    }
  }
  return { sentences, rest: (pending + text.slice(start)).trimStart() };
}
