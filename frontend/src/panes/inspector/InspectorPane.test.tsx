import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../../transport/client";
import type { BeliefDispute, Finding, Goal, MemoryItem } from "../../transport/types";

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
    personalityHistory: vi.fn().mockResolvedValue([]),
    revertChange: vi.fn().mockResolvedValue(undefined),
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
    correctMemory: vi.fn().mockResolvedValue({ ...MEMORY, id: "ep_2", trust: "user" }),
    disputes: vi.fn().mockResolvedValue([]),
    resolveDispute: vi.fn().mockResolvedValue(undefined),
    exportAll: vi.fn().mockResolvedValue(new ArrayBuffer(4)),
    documents: vi.fn().mockResolvedValue({ roots: [], documents: [] }),
    addDocuments: vi.fn().mockResolvedValue({
      added: 2,
      updated: 0,
      unchanged: 0,
      removed: 0,
      skipped: [{ path: "/notes/credentials.txt", reason: "looks like a secrets file" }],
    }),
    removeDocument: vi.fn().mockResolvedValue(undefined),
    forgetFolder: vi.fn().mockResolvedValue(undefined),
    purgePreview: vi.fn().mockResolvedValue({
      scope: "phrase",
      counts: { messages: 2, beliefs: 1 },
      examples: ["My sister Ana lives in Porto."],
      backups: 1,
      confirm: "abc123def456",
    }),
    purge: vi.fn().mockResolvedValue({
      id: "purge_1",
      scope_kind: "phrase",
      created_at: "",
      counts: { messages: 2, beliefs: 1 },
      verified: "file",
      also_elsewhere: 0,
      backups_deleted: 1,
    }),
    goals: vi.fn().mockResolvedValue([GOAL]),
    createGoal: vi.fn().mockResolvedValue(GOAL),
    updateGoal: vi.fn().mockResolvedValue({ ...GOAL, status: "active" }),
    goalNotes: vi.fn().mockResolvedValue([
      {
        id: "note_1",
        kind: "decision",
        text: "The user chose a Pratt parser.",
        source: "reflection",
        session_id: "s",
        created_at: "2026-11-20T10:00:00",
      },
    ]),
    addGoalNote: vi.fn().mockResolvedValue({}),
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

  it("shows how HEDWIG changed and why, and undoes a change", async () => {
    const api = fakeApi({
      personalityHistory: vi.fn().mockResolvedValue([
        {
          id: "hist_1",
          trait: "verbosity",
          delta: -0.02,
          source: "inferred",
          applied_at: "2026-11-21T03:00:00",
          reverted_at: null,
          rationale: 'the user\'s reactions: "wall of text"',
        },
      ]),
    });
    render(<InspectorPane api={api} />);
    const changes = await screen.findByRole("region", { name: "Recent changes" });
    expect(changes).toHaveTextContent("verbosity −0.020");
    expect(changes).toHaveTextContent("from your reactions");
    expect(changes).toHaveTextContent("wall of text");
    fireEvent.click(screen.getByRole("button", { name: "Revert" }));
    await waitFor(() => {
      expect(api.revertChange).toHaveBeenCalledWith("hist_1");
    });
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

  it("puts what the nightly run could not decide to the user, and settles it", async () => {
    const dispute: BeliefDispute = {
      pair: "bel_1|bel_2",
      verdict: "contradicts",
      proposed_at: "",
      first: { id: "bel_1", statement: "The user's sister lives in Porto." },
      second: { id: "bel_2", statement: "The user's sister lives in Lisbon." },
    };
    const api = fakeApi({
      disputes: vi.fn().mockResolvedValueOnce([dispute]).mockResolvedValue([]),
    });
    render(<InspectorPane api={api} />);
    tab("Memory");
    expect(await screen.findByRole("region", { name: "Needs your eye" })).toHaveTextContent(
      "Lisbon",
    );
    fireEvent.click(screen.getByRole("button", { name: "Second is true" }));
    await waitFor(() => {
      expect(api.resolveDispute).toHaveBeenCalledWith("bel_1|bel_2", "second");
    });
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Needs your eye" })).not.toBeInTheDocument();
    });
    expect(api.memories).toHaveBeenCalledTimes(2);
  });

  it("marks a belief that was replaced", async () => {
    const replaced: MemoryItem = { ...MEMORY, id: "bel_1", kind: "belief", status: "superseded" };
    render(<InspectorPane api={fakeApi({ memories: vi.fn().mockResolvedValue([replaced]) })} />);
    tab("Memory");
    expect(await screen.findByText(/no longer true/)).toBeInTheDocument();
  });

  it("says why a dispute could not be settled", async () => {
    const dispute: BeliefDispute = {
      pair: "bel_1|bel_2",
      verdict: "same",
      proposed_at: "",
      first: { id: "bel_1", statement: "The user enjoys green tea." },
      second: { id: "bel_2", statement: "The user drinks green tea daily." },
    };
    const api = fakeApi({
      disputes: vi.fn().mockResolvedValue([dispute]),
      resolveDispute: vi
        .fn()
        .mockRejectedValue(new ApiError("conflict", "That memory is pinned.", 409, null, false)),
    });
    render(<InspectorPane api={api} />);
    tab("Memory");
    fireEvent.click(await screen.findByRole("button", { name: "Same thing" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("pinned");
  });

  it("corrects a memory in place, in the user's words", async () => {
    const api = fakeApi();
    render(<InspectorPane api={api} />);
    tab("Memory");
    fireEvent.click(await screen.findByRole("button", { name: "Correct" }));
    const editor = screen.getByLabelText("Corrected memory");
    expect(editor).toHaveValue(MEMORY.text);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.change(editor, { target: { value: "User: my favourite tea is sencha" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(api.correctMemory).toHaveBeenCalledWith("ep_1", {
        text: "User: my favourite tea is sencha",
      });
    });
    await waitFor(() => {
      expect(api.memories).toHaveBeenCalledTimes(2);
    });
    expect(screen.queryByLabelText("Corrected memory")).not.toBeInTheDocument();
  });

  it("offers no correction for a belief that was already replaced", async () => {
    const replaced: MemoryItem = { ...MEMORY, kind: "belief", status: "superseded" };
    render(<InspectorPane api={fakeApi({ memories: vi.fn().mockResolvedValue([replaced]) })} />);
    tab("Memory");
    expect(await screen.findByText(/no longer true/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Correct" })).not.toBeInTheDocument();
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

  it("opens a project's journal and writes in it", async () => {
    const api = fakeApi();
    render(<InspectorPane api={api} />);
    tab("Goals");
    fireEvent.click(await screen.findByRole("button", { name: "Journal" }));
    expect(await screen.findByRole("region", { name: "Journal" })).toHaveTextContent(
      "decision: The user chose a Pratt parser. · from a conversation",
    );
    fireEvent.change(screen.getByLabelText("Journal note"), {
      target: { value: "Error recovery next." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Note" }));
    await waitFor(() => {
      expect(api.addGoalNote).toHaveBeenCalledWith("goal_1", "Error recovery next.", "decision");
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

  it("adds a folder of notes and says what it skipped", async () => {
    const api = fakeApi();
    render(<InspectorPane api={api} />);
    tab("Data");
    fireEvent.change(await screen.findByLabelText("Folder of notes"), {
      target: { value: "/notes" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => {
      expect(api.addDocuments).toHaveBeenCalledWith("/notes");
    });
    expect(
      await screen.findByText(/credentials.txt \(looks like a secrets file\)/),
    ).toBeInTheDocument();
    expect(api.documents).toHaveBeenCalledTimes(2);
  });

  it("downloads everything", async () => {
    const api = fakeApi();
    const save = vi.fn();
    render(<InspectorPane api={api} save={save} />);
    tab("Data");
    fireEvent.click(screen.getByRole("button", { name: "Download everything" }));
    await waitFor(() => {
      expect(save).toHaveBeenCalledWith(expect.any(ArrayBuffer), "hedwig-export.zip");
    });
  });

  it("erases only after showing exactly what will go", async () => {
    const api = fakeApi();
    render(<InspectorPane api={api} />);
    tab("Data");
    fireEvent.change(screen.getByLabelText("Words to erase"), { target: { value: "Ana" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));

    const preview = await screen.findByRole("region", { name: "Erase preview" });
    expect(preview).toHaveTextContent("2 messages");
    expect(preview).toHaveTextContent("1 backup");
    expect(api.purge).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Erase permanently" }));
    await waitFor(() => {
      expect(api.purge).toHaveBeenCalledWith("phrase", "Ana", "abc123def456");
    });
    expect(await screen.findByRole("status")).toHaveTextContent(
      "checked against the database file",
    );
  });

  it("says plainly when an erasure could not be verified", async () => {
    const api = fakeApi({
      purge: vi.fn().mockResolvedValue({
        id: "purge_1",
        scope_kind: "phrase",
        created_at: "",
        counts: {},
        verified: "failed",
        also_elsewhere: 0,
        backups_deleted: 0,
      }),
    });
    render(<InspectorPane api={api} />);
    tab("Data");
    fireEvent.change(screen.getByLabelText("Words to erase"), { target: { value: "Ana" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    fireEvent.click(await screen.findByRole("button", { name: "Erase permanently" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("traces were found");
  });

  it("moves between tabs with the arrow keys", () => {
    render(<InspectorPane api={fakeApi()} />);
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Memory" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "Mind" })).toHaveAttribute("aria-selected", "true");
  });
});
