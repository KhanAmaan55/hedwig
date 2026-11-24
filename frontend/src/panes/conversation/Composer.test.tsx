import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Microphone } from "../../voice/useMicrophone";

import { Composer, HOLD_MS } from "./Composer";

function microphone(overrides: Partial<Microphone> = {}): Microphone {
  return {
    available: true,
    reason: "",
    state: "idle",
    error: null,
    toggle: vi.fn().mockResolvedValue("water the tomatoes"),
    start: vi.fn().mockResolvedValue(true),
    stop: vi.fn().mockResolvedValue("water the tomatoes"),
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

  it("held, it is push-to-talk: release sends what was said (docs/15 §14)", async () => {
    const onSend = vi.fn().mockResolvedValue(true);
    const mic = microphone();
    const clock = vi.spyOn(performance, "now").mockReturnValue(1000);
    render(<Composer onSend={onSend} disabled={false} microphone={mic} />);
    const button = screen.getByRole("button", { name: "Mic" });

    fireEvent.pointerDown(button);
    clock.mockReturnValue(1000 + HOLD_MS + 50);
    fireEvent.pointerUp(button);

    await waitFor(() => {
      expect(onSend).toHaveBeenCalledWith("water the tomatoes");
    });
    expect(mic.start).toHaveBeenCalledTimes(1);
    expect(mic.stop).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Message HEDWIG")).toHaveValue("");
    clock.mockRestore();
  });

  it("held, if what was heard could not be sent, it is kept in the box", async () => {
    const onSend = vi.fn().mockResolvedValue(false);
    const clock = vi.spyOn(performance, "now").mockReturnValue(1000);
    render(<Composer onSend={onSend} disabled={false} microphone={microphone()} />);
    const button = screen.getByRole("button", { name: "Mic" });

    fireEvent.pointerDown(button);
    clock.mockReturnValue(1000 + HOLD_MS + 50);
    fireEvent.pointerUp(button);

    await waitFor(() => {
      expect(screen.getByLabelText("Message HEDWIG")).toHaveValue("water the tomatoes");
    });
    expect(onSend).toHaveBeenCalledWith("water the tomatoes");
    clock.mockRestore();
  });

  it("a press whose start failed does not make the next press a stop", () => {
    // Permission refused: the microphone never left idle, so every press is a fresh start.
    const mic = microphone({ start: vi.fn().mockResolvedValue(false) });
    render(<Composer onSend={vi.fn()} disabled={false} microphone={mic} />);
    const button = screen.getByRole("button", { name: "Mic" });

    fireEvent.pointerDown(button);
    fireEvent.pointerUp(button);
    fireEvent.pointerDown(button);
    fireEvent.pointerUp(button);

    expect(mic.start).toHaveBeenCalledTimes(2);
    expect(mic.stop).not.toHaveBeenCalled();
  });

  it("a cancelled press stops listening and sends nothing", async () => {
    const onSend = vi.fn().mockResolvedValue(true);
    const mic = microphone();
    render(<Composer onSend={onSend} disabled={false} microphone={mic} />);
    const button = screen.getByRole("button", { name: "Mic" });

    fireEvent.pointerDown(button);
    fireEvent.pointerCancel(button);
    fireEvent.pointerUp(button);

    await waitFor(() => {
      expect(mic.stop).toHaveBeenCalledTimes(1);
    });
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Message HEDWIG")).toHaveValue("");
  });

  it("clicked twice, it listens in between and puts what it heard in the box", async () => {
    const onSend = vi.fn().mockResolvedValue(true);
    const mic = microphone();
    const clock = vi.spyOn(performance, "now").mockReturnValue(1000);
    const { rerender } = render(<Composer onSend={onSend} disabled={false} microphone={mic} />);
    const button = screen.getByRole("button", { name: "Mic" });

    fireEvent.pointerDown(button);
    fireEvent.pointerUp(button);
    expect(mic.start).toHaveBeenCalledTimes(1);
    expect(mic.stop).not.toHaveBeenCalled(); // still listening
    rerender(
      <Composer onSend={onSend} disabled={false} microphone={{ ...mic, state: "recording" }} />,
    );
    clock.mockReturnValue(5000);
    fireEvent.pointerDown(button);
    fireEvent.pointerUp(button);

    await waitFor(() => {
      expect(screen.getByLabelText("Message HEDWIG")).toHaveValue("water the tomatoes");
    });
    expect(onSend).not.toHaveBeenCalled();
    clock.mockRestore();
  });
});
