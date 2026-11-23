import { describe, expect, it } from "vitest";

import { visemeAt } from "./visemes";

describe("visemeAt", () => {
  it("picks the shape of the vowel under the playhead", () => {
    expect(visemeAt("you", 0.5)).toBe("ou");
    expect(visemeAt("see", 0.4)).toBe("ee");
    expect(visemeAt("hot", 0.4)).toBe("oh");
    expect(visemeAt("cat", 0.4)).toBe("aa");
    expect(visemeAt("sit", 0.4)).toBe("ih");
  });

  it("holds the last vowel through consonants, and moves through a sentence", () => {
    const text = "moon then sea";
    expect(visemeAt(text, 0.1)).toBe("ou");
    expect(visemeAt(text, 0.55)).toBe("ee");
    expect(visemeAt(text, 0.99)).toBe("ee");
  });

  it("never fails on empty or vowel-less text", () => {
    expect(visemeAt("", 0.5)).toBe("aa");
    expect(visemeAt("hmm", 0.5)).toBe("aa");
  });
});
