import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../../transport/client";
import type { Explanation, MessagePage, SessionInfo, TurnReply } from "../../transport/types";

import { activityStore } from "../../stores/activity";
import { UNREACHABLE, type StreamHandlers } from "../../transport/socket";
import type { VoiceApi } from "../../voice/useVoice";

import ConversationPane from "./ConversationPane";
import type { ConversationApi, SessionStorage } from "./useConversation";

// -- fakes -------------------------------------------------------------------

function session(id: string): SessionInfo {
  return {
    id,
    channel: "web",
    started_at: "2026-11-20T09:00:00.000+00:00",
    ended_at: null,
    end_reason: null,
    turn_count: 0,
    last_active_at: "2026-11-20T09:00:00.000+00:00",
    is_open: true,
  };
}

function reply(text: string, cited: string[] = []): TurnReply {
  return {
    turn_id: "turn_1",
    session_id: "sess_1",
    user_message_id: "msg_u",
    message_id: `msg_${text.length}`,
    reply: text,
    status: "completed",
    intent: "answer",
    cited_memory_ids: cited,
    mood: {},
  };
}

function fakeApi(overrides: Partial<ConversationApi> = {}) {
  return {
    openSession: vi.fn().mockResolvedValue(session("sess_1")),
    history: vi.fn().mockResolvedValue({ messages: [], next_before: null } satisfies MessagePage),
    send: vi.fn().mockResolvedValue(reply("Hello! Lovely to meet you.")),
    endSession: vi.fn().mockResolvedValue(session("sess_1")),
    ...overrides,
  };
}

function memoryStorage(initial: string | null = null): SessionStorage & { value: string | null } {
  return {
    value: initial,
    get() {
      return this.value;
    },
    set(id) {
      this.value = id;
    },
    clear() {
      this.value = null;
    },
  };
}

function apiError(code: string, status: number, detail: Record<string, unknown> = {}): ApiError {
  return new ApiError(code, code, status, null, status >= 500, detail);
}

const composer = () => screen.getByLabelText<HTMLTextAreaElement>("Message HEDWIG");

function type(text: string): void {
  fireEvent.change(composer(), { target: { value: text } });
}

function pressEnter(shift = false): void {
  fireEvent.keyDown(composer(), { key: "Enter", shiftKey: shift });
}

// -- tests -------------------------------------------------------------------

describe("ConversationPane", () => {
  it("opens a session on the first message, not on load", async () => {
    const api = fakeApi();
    const storage = memoryStorage();
    render(<ConversationPane api={api} storage={storage} />);

    expect(screen.getByText(/Say hello/)).toBeInTheDocument();
    expect(api.openSession).not.toHaveBeenCalled();

    type("Hey HEDWIG!");
    pressEnter();

    expect(await screen.findByText("Hello! Lovely to meet you.")).toBeInTheDocument();
    expect(screen.getByText("Hey HEDWIG!")).toBeInTheDocument();
    expect(api.openSession).toHaveBeenCalledWith("web");
    expect(api.send).toHaveBeenCalledWith("sess_1", "Hey HEDWIG!");
    expect(storage.value).toBe("sess_1");
    expect(composer().value).toBe("");
  });

  it("shows that HEDWIG is thinking and waits for one turn at a time", async () => {
    let finish: (value: TurnReply) => void = () => undefined;
    const api = fakeApi({
      send: vi.fn(
        () =>
          new Promise<TurnReply>((resolve) => {
            finish = resolve;
          }),
      ),
    });
    render(<ConversationPane api={api} storage={memoryStorage()} />);

    type("first");
    pressEnter();

    expect(await screen.findByRole("status")).toHaveTextContent("thinking");
    // The next thought can be drafted, but not sent until this turn finishes.
    type("second");
    pressEnter();
    expect(api.send).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();

    await act(async () => {
      finish(reply("done"));
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Send" })).not.toBeDisabled();
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(composer().value).toBe("second");
  });

  it("Shift+Enter is a new line, not a send", () => {
    const api = fakeApi();
    render(<ConversationPane api={api} storage={memoryStorage()} />);

    type("line one");
    pressEnter(true);

    expect(api.send).not.toHaveBeenCalled();
  });

  it("says how many memories a reply drew on", async () => {
    const api = fakeApi({ send: vi.fn().mockResolvedValue(reply("Fernweh.", ["m1", "m2"])) });
    render(<ConversationPane api={api} storage={memoryStorage()} />);

    type("What is my compiler called?");
    pressEnter();

    expect(await screen.findByText("drew on 2 memories")).toBeInTheDocument();
  });

  it("resumes the last conversation from where it left off", async () => {
    const api = fakeApi({
      history: vi.fn().mockResolvedValue({
        messages: [
          { id: "m1", seq: 1, role: "user", text: "We were on memory.", created_at: "" },
          { id: "m2", seq: 2, role: "hedwig", text: "Consolidation, yes.", created_at: "" },
        ],
        next_before: null,
      } satisfies MessagePage),
    });
    render(<ConversationPane api={api} storage={memoryStorage("sess_old")} />);

    expect(await screen.findByText("Consolidation, yes.")).toBeInTheDocument();
    expect(api.history).toHaveBeenCalledWith("sess_old", 50);

    type("Where were we?");
    pressEnter();
    await waitFor(() => {
      expect(api.send).toHaveBeenCalledWith("sess_old", "Where were we?");
    });
    expect(api.openSession).not.toHaveBeenCalled();
  });

  it("forgets a stale conversation quietly and starts a new one", async () => {
    const storage = memoryStorage("sess_gone");
    const api = fakeApi({ history: vi.fn().mockRejectedValue(apiError("not_found", 404)) });
    render(<ConversationPane api={api} storage={storage} />);

    expect(await screen.findByText(/Say hello/)).toBeInTheDocument();
    expect(storage.value).toBeNull();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows HEDWIG's own sentence when the model is down, and why", async () => {
    const api = fakeApi({
      send: vi.fn().mockRejectedValue(
        apiError("capability_unavailable", 503, {
          reply: "I can't think right now — my language model isn't running.",
        }),
      ),
    });
    render(<ConversationPane api={api} storage={memoryStorage()} />);

    type("Are you there?");
    pressEnter();

    expect(await screen.findByText(/my language model isn't running/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("hedwig models");
    // The turn happened: the message stays and the composer is clear.
    expect(screen.getByText("Are you there?")).toBeInTheDocument();
    expect(composer().value).toBe("");
  });

  it("gives the text back when nothing reached HEDWIG", async () => {
    const api = fakeApi({ send: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")) });
    render(<ConversationPane api={api} storage={memoryStorage()} />);

    type("hello?");
    pressEnter();

    expect(await screen.findByRole("alert")).toHaveTextContent("hedwig serve");
    expect(composer().value).toBe("hello?");
    // Taken back, so resending does not show it twice.
    expect(screen.queryByText("hello?", { selector: ".message__text" })).not.toBeInTheDocument();
  });

  it("new conversation starts over", async () => {
    const storage = memoryStorage();
    const api = fakeApi();
    render(<ConversationPane api={api} storage={storage} />);

    type("hi there");
    pressEnter();
    await screen.findByText("Hello! Lovely to meet you.");

    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));

    expect(screen.getByText(/Say hello/)).toBeInTheDocument();
    expect(storage.value).toBeNull();
    // The old conversation is ended now, not half an hour from now (FND-03).
    expect(api.endSession).toHaveBeenCalledWith("sess_1");
  });

  it("starts over even when ending the old conversation fails", async () => {
    const api = fakeApi({ endSession: vi.fn().mockRejectedValue(new TypeError("offline")) });
    render(<ConversationPane api={api} storage={memoryStorage()} />);

    type("hi there");
    pressEnter();
    await screen.findByText("Hello! Lovely to meet you.");
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));

    expect(screen.getByText(/Say hello/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders a reply as text, never as markup", async () => {
    const api = fakeApi({ send: vi.fn().mockResolvedValue(reply("<img src=x onerror=alert(1)>")) });
    const { container } = render(<ConversationPane api={api} storage={memoryStorage()} />);

    type("try me");
    pressEnter();

    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});

describe("ConversationPane voice (docs/15 §13)", () => {
  function voiceApi(available: boolean): VoiceApi & { speak: ReturnType<typeof vi.fn> } {
    return {
      speechStatus: vi.fn().mockResolvedValue({
        available,
        engine: "piper",
        voice: "en_US-lessac-medium",
        reason: available ? "" : "piper is not installed",
      }),
      speak: vi.fn().mockReturnValue(new Promise(() => undefined)),
    };
  }

  it("shows thinking on the click, then speaks the reply without audio when voice is off", async () => {
    const voice = voiceApi(true);
    render(<ConversationPane api={fakeApi()} storage={memoryStorage()} voiceApi={voice} />);

    type("hi there");
    pressEnter();
    expect(activityStore.get()).toBe("thinking");

    await screen.findByText("Hello! Lovely to meet you.");
    await waitFor(() => {
      expect(activityStore.get()).toBe("speaking");
    });
    expect(voice.speak).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(activityStore.get()).toBe("idle");
  });

  it("speaks the reply through the backend when voice is on", async () => {
    window.localStorage.setItem("hedwig.voice.enabled", "1");
    const voice = voiceApi(true);
    render(<ConversationPane api={fakeApi()} storage={memoryStorage()} voiceApi={voice} />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Voice on" })).not.toBeDisabled();
    });

    type("hi there");
    pressEnter();

    await waitFor(() => {
      expect(voice.speak).toHaveBeenCalledWith("Hello! Lovely to meet you.");
    });
    window.localStorage.removeItem("hedwig.voice.enabled");
  });

  it("explains why the voice cannot be switched on", async () => {
    render(
      <ConversationPane api={fakeApi()} storage={memoryStorage()} voiceApi={voiceApi(false)} />,
    );
    const toggle = await screen.findByRole("button", { name: "Voice off" });
    await waitFor(() => {
      expect(toggle).toHaveAttribute("title", "piper is not installed");
    });
    expect(toggle).toBeDisabled();
  });
});

describe("ConversationPane streaming (docs/16 §13)", () => {
  it("shows the stage, then the words as they arrive, then the final reply", async () => {
    let finish: (reply: TurnReply) => void = () => undefined;
    let handlers: StreamHandlers = {};
    const live = {
      send: vi.fn((_session: string | null, _text: string, given: StreamHandlers = {}) => {
        handlers = given;
        return new Promise<TurnReply>((resolve) => {
          finish = resolve;
        });
      }),
    };
    const api = fakeApi();
    const storage = memoryStorage();
    render(<ConversationPane api={api} storage={storage} live={live} />);

    type("hello there");
    pressEnter();
    await waitFor(() => {
      expect(live.send).toHaveBeenCalled();
    });

    act(() => {
      handlers.onStarted?.("sess_live", "t1");
      handlers.onStage?.("recalling", null);
    });
    expect(screen.getByRole("status")).toHaveTextContent("remembering");

    act(() => {
      handlers.onToken?.("Hello ");
      handlers.onToken?.("from the stream");
    });
    expect(screen.getByText("Hello from the stream")).toBeInTheDocument();
    expect(activityStore.get()).toBe("speaking");

    await act(async () => {
      finish({ ...reply("Hello from the stream."), message_id: "msg_final" });
      await Promise.resolve();
    });
    expect(await screen.findByText("Hello from the stream.")).toBeInTheDocument();
    expect(screen.queryByText("Hello from the stream")).not.toBeInTheDocument();
    expect(storage.value).toBe("sess_live");
    expect(api.openSession).not.toHaveBeenCalled();
    expect(api.send).not.toHaveBeenCalled();
  });

  it("stops a reply mid-sentence", async () => {
    let handlers: StreamHandlers = {};
    const live = {
      send: vi.fn((_session: string | null, _text: string, given: StreamHandlers = {}) => {
        handlers = given;
        return new Promise<TurnReply>(() => undefined);
      }),
      interrupt: vi.fn(),
    };
    render(<ConversationPane api={fakeApi()} storage={memoryStorage()} live={live} />);
    type("tell me a long story");
    pressEnter();
    await waitFor(() => {
      expect(live.send).toHaveBeenCalled();
    });
    act(() => {
      handlers.onStarted?.("sess_live", "turn_9");
      handlers.onToken?.("Once upon ");
    });

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));

    expect(live.interrupt).toHaveBeenCalledWith("turn_9");
  });

  it("falls back to REST when the live channel cannot open", async () => {
    const live = {
      send: vi.fn().mockRejectedValue(new ApiError(UNREACHABLE, "no socket", 0, null, true)),
    };
    const api = fakeApi();
    render(<ConversationPane api={api} storage={memoryStorage()} live={live} />);

    type("hello there");
    pressEnter();

    expect(await screen.findByText("Hello! Lovely to meet you.")).toBeInTheDocument();
    expect(api.send).toHaveBeenCalledWith("sess_1", "hello there");
  });
});

describe("Why? (docs/16 §14)", () => {
  const WHY: Explanation = {
    message_id: "msg_9",
    question: "What tea do I like?",
    reply: "Lapsang souchong.",
    intent: "answer",
    rationale: "answering from what is already known",
    directives: ["Be concise but complete."],
    goals_in_view: 0,
    finding_offered: false,
    mood: { happiness: 0.6, stress: 0.1 },
    retrieval: {
      queries: ["What tea do I like?"],
      items: [
        {
          memory_id: "ep_1",
          score: 0.83,
          trust: "user",
          kind: "episode",
          text: "User: my favourite tea is lapsang",
          forgotten_since: false,
        },
      ],
      dropped_count: 0,
    },
    tools: [],
    model: [
      {
        purpose: "compose.reply",
        model: "gemma4:e2b",
        status: "ok",
        tokens_in: 900,
        tokens_out: 12,
        duration_ms: 400,
      },
    ],
    trace_id: "req_1",
  };

  it("shows what HEDWIG remembered, how it was told to speak, and its mood — once asked", async () => {
    const explainApi = { explain: vi.fn().mockResolvedValue(WHY) };
    const api = fakeApi({
      send: vi.fn().mockResolvedValue({ ...reply("Lapsang souchong."), message_id: "msg_9" }),
    });
    render(<ConversationPane api={api} storage={memoryStorage()} explainApi={explainApi} />);

    type("What tea do I like?");
    pressEnter();
    const why = await screen.findByRole("button", { name: "Why?" });
    expect(explainApi.explain).not.toHaveBeenCalled();

    fireEvent.click(why);

    expect(await screen.findByText("User: my favourite tea is lapsang")).toBeInTheDocument();
    expect(screen.getByText(/you told me, relevance 0.83/)).toBeInTheDocument();
    expect(screen.getByText("Be concise but complete.")).toBeInTheDocument();
    expect(screen.getByText(/not feelings/)).toBeInTheDocument();
    expect(screen.getByText(/gemma4:e2b, 900 tokens in, 12 out/)).toBeInTheDocument();
    expect(explainApi.explain).toHaveBeenCalledWith("msg_9");

    fireEvent.click(why);
    expect(screen.queryByText("Be concise but complete.")).not.toBeInTheDocument();
  });

  it("is honest about what was not recorded or not done", async () => {
    const explainApi = {
      explain: vi.fn().mockResolvedValue({ ...WHY, directives: null, retrieval: null, mood: null }),
    };
    const api = fakeApi({
      send: vi.fn().mockResolvedValue({ ...reply("Hi."), message_id: "msg_9" }),
    });
    render(<ConversationPane api={api} storage={memoryStorage()} explainApi={explainApi} />);

    type("hello there");
    pressEnter();
    fireEvent.click(await screen.findByRole("button", { name: "Why?" }));

    expect(await screen.findByText("Did not search memory.")).toBeInTheDocument();
    expect(screen.getByText("Not recorded for replies this old.")).toBeInTheDocument();
  });
});
