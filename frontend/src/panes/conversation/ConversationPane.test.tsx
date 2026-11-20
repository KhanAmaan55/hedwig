import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../../transport/client";
import type { MessagePage, SessionInfo, TurnReply } from "../../transport/types";

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
