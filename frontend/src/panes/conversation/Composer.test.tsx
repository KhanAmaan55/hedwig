import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Microphone } from "../../voice/useMicrophone";

import { Composer } from "./Composer";

function microphone(overrides: Partial<Microphone> = {}): Microphone {
  return {
    available: true,
    reason: "",
    state: "idle",
    error: null,
    toggle: vi.fn().mockResolvedValue("water the tomatoes"),
    ...overrides,
  };
}

describe("Composer speech input (VOI-02)", () => {
  it("puts what was heard in the box, and sends nothing by itself", async () => {
    const onSend = vi.fn().mockResolvedValue(true);
    render(<Composer onSend={onSend} disabled={false} microphone={microphone()} />);

    fireEvent.click(screen.getByRole("button", { name: "Mic" }));

    await waitFor(() => {
      expect(screen.getByLabelText("Message HEDWIG")).toHaveValue("water the tomatoes");
    });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("shows no microphone when HEDWIG cannot listen", () => {
    render(
      <Composer onSend={vi.fn()} disabled={false} microphone={microphone({ available: false })} />,
    );
    expect(screen.queryByRole("button", { name: "Mic" })).not.toBeInTheDocument();
  });

  it("says why it could not hear", () => {
    render(
      <Composer
        onSend={vi.fn()}
        disabled={false}
        microphone={microphone({ error: "The microphone is not available." })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("microphone is not available");
  });
});
