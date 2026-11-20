import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PaneBoundary } from "./PaneBoundary";

function Broken(): never {
  throw new Error("boom");
}

describe("PaneBoundary", () => {
  it("keeps one pane's failure to that pane", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <>
        <PaneBoundary name="Inspector">
          <Broken />
        </PaneBoundary>
        <p>the rest of the app</p>
      </>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Inspector");
    expect(screen.getByText("the rest of the app")).toBeInTheDocument();
  });
});
