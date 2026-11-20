import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../../transport/client";
import type { Finding, Goal, MemoryItem } from "../../transport/types";

import InspectorPane, { type InspectorApi } from "./InspectorPane";

const MEMORY: MemoryItem = {
  id: "ep_1",
  kind: "episode",
  text: "User: my favourite tea is lapsang",
  occurred_at: "",
  salience: 0.7,
  confidence: 1,
  pinned: false,
  forgotten: false,
  trust: "user",
  source: null,
  status: null,
};
const GOAL: Goal = {
  id: "goal_1",
  description: "Help the user learn Portuguese",
  kind: "self_generated",
  status: "proposed",
  priority: 0.5,
  progress: 0,
  context: "",
  completion: "",
  origin: "reflection",
  created_at: "",
  last_touched_at: "",
};
const FINDING: Finding = {
  id: "fnd_1",
  question: "How do agents remember?",
  summary: "Tiered memory and reflection [1].",
  sources: [{ title: "MemGPT", url: "https://arxiv.org/abs/2310.08560" }],
  status: "new",
  trust: "untrusted",
  created_at: "",
};

function fakeApi(overrides: Partial<InspectorApi> = {}): InspectorApi {
  return {
    emotion: vi.fn().mockResolvedValue({
      happiness: 0.6,
      trust: 0.6,
      curiosity: 0.7,
      confidence: 0.6,
      energy: 0.5,
      stress: 0.1,
      valence: 0.2,
      arousal: 0.3,
    }),
    personality: vi.fn().mockResolvedValue({
      traits: [{ trait: "warmth", value: 0.7, anchor: 0.7, lifetime_drift: 0 }],
      version: 1,
    }),
    relationship: vi.fn().mockResolvedValue({
      familiarity: 0.1,
      affinity: 0,
      trust: 0.5,
      interaction_count: 3,
      last_interaction_at: null,
    }),
    memories: vi.fn().mockResolvedValue([MEMORY]),
    searchMemory: vi
      .fn()
      .mockResolvedValue({ query: "tea", hits: [{ memory: MEMORY, score: 1, breakdown: {} }] }),
    memoryAction: vi.fn().mockResolvedValue({ ...MEMORY, forgotten: true }),
    goals: vi.fn().mockResolvedValue([GOAL]),
    createGoal: vi.fn().mockResolvedValue(GOAL),
    updateGoal: vi.fn().mockResolvedValue({ ...GOAL, status: "active" }),
    gaps: vi.fn().mockResolvedValue([]),
    askCuriosity: vi
      .fn()
      .mockRejectedValue(
        new ApiError("invalid_request", "That question mentions you.", 400, null, false),
      ),
    findings: vi.fn().mockResolvedValue([FINDING]),
    findingAction: vi.fn().mockResolvedValue({ ...FINDING, status: "confirmed" }),
    ...overrides,
  };
}

const tab = (name: string) => {
  fireEvent.click(screen.getByRole("tab", { name }));
};

describe("InspectorPane", () => {
  it("shows the mood, the relationship and the personality, and says what the mood is", async () => {
    render(<InspectorPane api={fakeApi()} />);
    expect(await screen.findByRole("meter", { name: "curiosity" })).toHaveAttribute(
      "aria-valuenow",
      "70",
    );
    expect(screen.getByText(/not feelings/)).toBeInTheDocument();
    expect(await screen.findByText("3 turns together.")).toBeInTheDocument();
    expect(await screen.findByRole("meter", { name: "warmth" })).toBeInTheDocument();
  });

  it("forgets a memory on the user's word", async () => {
    const api = fakeApi();
    render(<InspectorPane api={api} />);
    tab("Memory");
    fireEvent.click(await screen.findByRole("button", { name: "Forget" }));
    await waitFor(() => {
      expect(api.memoryAction).toHaveBeenCalledWith("ep_1", "forget");
    });
    await waitFor(() => {
      expect(api.memories).toHaveBeenCalledTimes(2);
    });
  });

  it("searches as HEDWIG recalls", async () => {
    const api = fakeApi();
    render(<InspectorPane api={api} />);
    tab("Memory");
    fireEvent.change(screen.getByLabelText("Search memory"), { target: { value: "tea" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => {
      expect(api.searchMemory).toHaveBeenCalledWith("tea");
    });
  });

  it("lets the user accept a goal HEDWIG proposed", async () => {
    const api = fakeApi();
    render(<InspectorPane api={api} />);
    tab("Goals");
    expect(await screen.findByText("Proposed by HEDWIG from a conversation.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    await waitFor(() => {
      expect(api.updateGoal).toHaveBeenCalledWith("goal_1", { status: "active" });
    });
  });

  it("keeps a finding, links its sources, and says why a question was refused", async () => {
    const api = fakeApi();
    render(<InspectorPane api={api} />);
    tab("Curiosity");
    expect(await screen.findByRole("link", { name: "MemGPT" })).toHaveAttribute(
      "href",
      "https://arxiv.org/abs/2310.08560",
    );
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    await waitFor(() => {
      expect(api.findingAction).toHaveBeenCalledWith("fnd_1", "confirm");
    });

    fireEvent.change(screen.getByLabelText("Something to find out"), {
      target: { value: "Why is my code slow?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("mentions you");
  });

  it("moves between tabs with the arrow keys", () => {
    render(<InspectorPane api={fakeApi()} />);
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Memory" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "Mind" })).toHaveAttribute("aria-selected", "true");
  });
});
