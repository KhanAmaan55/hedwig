import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExpressionIntent } from "../../transport/types";

import type { AvatarStatus, VrmAvatarProps } from "./VrmAvatar";

// jsdom has no WebGL, so the renderer is replaced by a stand-in that reports whatever status a
// test asks for. Everything above it — the pane's states, the licence line, the readout — is real.
let nextStatus: AvatarStatus = { state: "loading" };
let lastProps: VrmAvatarProps | null = null;
vi.mock("./VrmAvatar", () => ({
  VrmAvatar: (props: VrmAvatarProps) => {
    lastProps = props;
    queueMicrotask(() => props.onStatus?.(nextStatus));
    return <div data-testid="vrm" />;
  },
}));

const { default: AvatarPane } = await import("./AvatarPane");

const AMUSED: ExpressionIntent = {
  mood: { valence: 0.52, arousal: 0.41 },
  overlay: { amusement: 0.6, warmth: 0.3, fatigue: 0.01 },
  gaze: "user",
  energy: 0.6,
  action: "idle",
  emotion_version: 7,
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve(AMUSED) }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  nextStatus = { state: "loading" };
  lastProps = null;
});

describe("AvatarPane", () => {
  it("says plainly when the display cannot run WebGL", () => {
    render(<AvatarPane webgl={false} />);
    expect(screen.getByRole("status")).toHaveTextContent("can’t run WebGL");
  });

  it("explains how to provide a model when there is none", async () => {
    nextStatus = { state: "missing", reason: "nothing at avatars/hedwig.vrm (404)" };
    render(<AvatarPane webgl />);
    expect(await screen.findByText("No avatar model yet.")).toBeInTheDocument();
    expect(screen.getByText(/frontend\/public\/avatars\/hedwig.vrm/)).toBeInTheDocument();
  });

  it("shows a load error without crashing", async () => {
    nextStatus = { state: "error", message: "this file is glTF but not VRM" };
    render(<AvatarPane webgl />);
    expect(await screen.findByText("this file is glTF but not VRM")).toBeInTheDocument();
  });

  it("names the model, its author and its licence once loaded", async () => {
    nextStatus = {
      state: "ready",
      model: {
        name: "Sample",
        authors: "pixiv",
        license: "VRM Public License · avatar use: everyone",
        licenseUrl: "https://vrm.dev/licenses/1.0/",
        version: "1",
      },
    };
    render(<AvatarPane webgl />);
    const link = await screen.findByRole("link", { name: /VRM Public License/ });
    expect(link).toHaveAttribute("href", "https://vrm.dev/licenses/1.0/");
    expect(screen.getByText(/Sample · pixiv/)).toBeInTheDocument();
  });

  it("hands the renderer the expression from the backend — the emotion API", async () => {
    render(<AvatarPane webgl />);
    await waitFor(() => {
      expect(lastProps?.expression).toEqual(AMUSED);
    });
  });

  it("shows what the face is being asked to express, strongest first", async () => {
    render(<AvatarPane webgl />);
    const readout = await screen.findByLabelText("Current expression");
    expect(readout).toHaveTextContent("amusement 0.60 · warmth 0.30");
    expect(readout).not.toHaveTextContent("fatigue"); // below the display threshold
  });

  it("says when it has lost the mood feed, and keeps the avatar", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    render(<AvatarPane webgl />);
    expect(await screen.findByText("mood offline")).toBeInTheDocument();
    expect(screen.getByTestId("vrm")).toBeInTheDocument();
  });

  it("loads a dropped .vrm instead of the default", async () => {
    const created = vi.fn().mockReturnValue("blob:dropped");
    vi.stubGlobal(
      "URL",
      Object.assign(URL, { createObjectURL: created, revokeObjectURL: vi.fn() }),
    );
    const { container } = render(<AvatarPane webgl />);
    const pane = container.querySelector(".avatar");
    expect(pane).not.toBeNull();

    const file = new File(["glTF"], "mine.vrm");
    await act(async () => {
      pane?.dispatchEvent(
        Object.assign(new Event("drop", { bubbles: true }), {
          dataTransfer: { files: [file] },
        }),
      );
      await Promise.resolve();
    });

    expect(created).toHaveBeenCalledWith(file);
    expect(lastProps?.src).toBe("blob:dropped");
  });
});
