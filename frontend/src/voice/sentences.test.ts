import { describe, expect, it } from "vitest";

import { takeSentences } from "./sentences";

describe("takeSentences (docs/15 §14)", () => {
  it("splits off complete sentences and keeps what is still being written", () => {
    expect(takeSentences("Porto is lovely in June. The trai")).toEqual({
      sentences: ["Porto is lovely in June."],
      rest: "The trai",
    });
  });

  it("waits for whitespace after the stop: it may be a decimal or an ellipsis", () => {
    expect(takeSentences("It costs 3.5 euro")).toEqual({
      sentences: [],
      rest: "It costs 3.5 euro",
    });
    expect(takeSentences("That is all.")).toEqual({ sentences: [], rest: "That is all." });
  });

  it("does not end a sentence at an abbreviation", () => {
    expect(takeSentences("Ask Dr. Silva about the knee. Then rest ").sentences).toEqual([
      "Ask Dr. Silva about the knee.",
    ]);
  });

  it("joins a very short sentence to the next", () => {
    expect(takeSentences("Yes! The train is the better choice. ").sentences).toEqual([
      "Yes! The train is the better choice.",
    ]);
  });

  it("keeps closing quotes and ends at questions, exclamations and blank lines", () => {
    expect(
      takeSentences('She said "go by train." Would that work for you? Great!\n\nNext ').sentences,
    ).toEqual(['She said "go by train."', "Would that work for you?", "Great!"]);
  });
});
