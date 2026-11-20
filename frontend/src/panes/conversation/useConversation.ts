import { useCallback, useEffect, useRef, useState } from "react";

import { activityStore } from "../../stores/activity";
import { ApiError, client as defaultClient, type HedwigClient } from "../../transport/client";
import { DISCONNECTED, UNREACHABLE, type StreamApi } from "../../transport/socket";
import type { TurnReply } from "../../transport/types";

/**
 * The conversation pane's state (docs/18 §13.3).
 *
 * One hook over three REST calls. The stores of docs/18 §4 arrive with the WebSocket reducer
 * (FND-05); until then this is the whole of it, and it is what a test drives.
 */

export type ConversationApi = Pick<HedwigClient, "openSession" | "history" | "send" | "endSession">;

export interface SessionStorage {
  get(): string | null;
  set(sessionId: string): void;
  clear(): void;
}

export interface ChatMessage {
  readonly key: string;
  readonly role: "user" | "hedwig";
  readonly text: string;
  /** The reply came from a turn that failed — HEDWIG saying it cannot answer right now. */
  readonly failed?: boolean;
  /** How many memories this reply drew on (docs/18 §13.3). */
  readonly recalled?: number;
  /** Still arriving, token by token (docs/16 §13). */
  readonly streaming?: boolean;
}

export interface Conversation {
  readonly sessionId: string | null;
  readonly messages: readonly ChatMessage[];
  /** A turn is in flight. The composer waits: one turn at a time. */
  readonly busy: boolean;
  /** Resuming the previous conversation. */
  readonly loading: boolean;
  readonly error: string | null;
  /** What HEDWIG is doing in this turn, from the live channel: "recalling", "composing"… */
  readonly stage: string | null;
  /** Resolves false when nothing was sent, so the composer can keep the text. */
  readonly send: (text: string) => Promise<boolean>;
  readonly startOver: () => void;
}

const STORAGE_KEY = "hedwig.conversation.session";
const RESUME_LIMIT = 50;

/** `localStorage`, wrapped: it can be absent or throw (private windows, blocked storage). */
export const browserStorage: SessionStorage = {
  get() {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  },
  set(sessionId) {
    try {
      window.localStorage.setItem(STORAGE_KEY, sessionId);
    } catch {
      // A convenience, not a record: the server holds the conversation.
    }
  },
  clear() {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // As above.
    }
  },
};

export function describeError(caught: unknown): string {
  if (caught instanceof ApiError) {
    switch (caught.code) {
      case "capability_unavailable":
        return "The language model is unavailable. Run `hedwig models` to see what is missing.";
      case "unauthorized":
        return "The API token was refused. Restart the app so it can read the current token.";
      case "not_found":
        return "That conversation no longer exists. Send again to start a new one.";
      case DISCONNECTED:
        return caught.message;
      default:
        return `${caught.code}: ${caught.message}`;
    }
  }
  return "HEDWIG is not reachable. Start it with `npm run dev` or `uv run hedwig serve`.";
}

export function useConversation(
  api: ConversationApi = defaultClient,
  storage: SessionStorage = browserStorage,
  onReply?: (text: string, streamed: boolean) => void,
  live: StreamApi | null = null,
): Conversation {
  const [sessionId, setSessionId] = useState<string | null>(() => storage.get());
  const [messages, setMessages] = useState<readonly ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(() => storage.get() !== null);
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState<string | null>(null);
  const counter = useRef(0);
  const nextKey = () => `local-${++counter.current}`;

  // Resume where the last window left off. Once, on mount.
  const resumeFrom = useRef(sessionId);
  useEffect(() => {
    const resumed = resumeFrom.current;
    if (resumed === null) return;
    let cancelled = false;

    api
      .history(resumed, RESUME_LIMIT)
      .then((page) => {
        if (cancelled) return;
        setMessages(
          page.messages.flatMap((message): ChatMessage[] =>
            message.role === "user" || message.role === "hedwig"
              ? [{ key: message.id, role: message.role, text: message.text }]
              : [],
          ),
        );
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        // A stale id is not an error worth showing: start fresh, quietly.
        if (caught instanceof ApiError && caught.code === "not_found") {
          storage.clear();
          setSessionId(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [api, storage]);

  const send = useCallback(
    async (text: string): Promise<boolean> => {
      const trimmed = text.trim();
      if (!trimmed || busy) return false;

      /** One turn over the live channel. `null` when the channel could not carry it at all. */
      const streamTurn = async (channel: StreamApi, text: string): Promise<TurnReply | null> => {
        const key = nextKey();
        let shown = false;
        let quiet = 0;
        try {
          return await channel.send(sessionId, text, {
            onStarted: (started) => {
              if (started !== sessionId) {
                setSessionId(started);
                storage.set(started);
              }
            },
            onStage: (name) => {
              setStage(name);
            },
            onToken: (piece) => {
              if (!shown) {
                shown = true;
                setStage(null);
                setMessages((current) => [
                  ...current,
                  { key, role: "hedwig", text: "", streaming: true },
                ]);
                activityStore.set("speaking");
              }
              setMessages((current) =>
                current.map((m) => (m.key === key ? { ...m, text: m.text + piece } : m)),
              );
              // The mouth moves with the words as they arrive, and rests between them.
              activityStore.setMouth(0.65);
              window.clearTimeout(quiet);
              quiet = window.setTimeout(() => {
                activityStore.setMouth(0.1);
              }, 90);
            },
          });
        } catch (caught) {
          window.clearTimeout(quiet);
          if (caught instanceof ApiError && caught.code === UNREACHABLE) return null;
          if (caught instanceof ApiError && caught.code === DISCONNECTED) {
            throw new ApiError(DISCONNECTED, caught.message, 0, null, true);
          }
          throw caught;
        } finally {
          window.clearTimeout(quiet);
        }
      };

      const userKey = nextKey();
      setMessages((current) => [...current, { key: userKey, role: "user", text: trimmed }]);
      setBusy(true);
      setError(null);
      // The face reacts on the click, not on the next poll (docs/15 §13).
      activityStore.set("thinking");

      try {
        const streamed = live ? await streamTurn(live, trimmed) : null;
        let reply: TurnReply;
        if (streamed) {
          reply = streamed;
        } else {
          // REST: no live channel, or it could not be opened before anything was sent.
          let target = sessionId;
          if (target === null) {
            // Lazily: opening the app must not create empty conversations (docs/18 §13.3).
            target = (await api.openSession("web")).id;
            setSessionId(target);
            storage.set(target);
          }
          reply = await api.send(target, trimmed);
        }
        const finished: ChatMessage = {
          key: reply.message_id,
          role: "hedwig",
          text: reply.reply,
          recalled: reply.cited_memory_ids.length,
        };
        // The streamed text was a preview; the reply is authoritative (ADR-0023).
        setMessages((current) => [...current.filter((m) => !m.streaming), finished]);
        activityStore.set("idle");
        onReply?.(reply.reply, streamed !== null);
        return true;
      } catch (caught) {
        setMessages((current) => current.filter((m) => !m.streaming));
        setError(describeError(caught));
        const honest =
          caught instanceof ApiError && typeof caught.detail.reply === "string"
            ? caught.detail.reply
            : null;
        if (honest) {
          // The turn happened and failed honestly: keep the message, show what HEDWIG said.
          setMessages((current) => [
            ...current,
            { key: nextKey(), role: "hedwig", text: honest, failed: true },
          ]);
          return true;
        }
        // Nothing reached HEDWIG. Take the message back so resending does not duplicate it.
        setMessages((current) => current.filter((message) => message.key !== userKey));
        if (caught instanceof ApiError && caught.code === "not_found") {
          storage.clear();
          setSessionId(null);
        }
        return false;
      } finally {
        setBusy(false);
        setStage(null);
        if (activityStore.get() !== "idle" && activityStore.get() !== "speaking") {
          activityStore.set("idle");
        }
      }
    },
    [api, busy, sessionId, storage, onReply, live],
  );

  const startOver = useCallback(() => {
    // End the old conversation now (FND-03). Best effort: if it fails, the idle reaper ends it,
    // and starting over must not wait on it or be blocked by it.
    if (sessionId !== null) void api.endSession(sessionId).catch(() => undefined);
    storage.clear();
    setSessionId(null);
    setMessages([]);
    setError(null);
  }, [api, sessionId, storage]);

  return { sessionId, messages, busy, loading, error, stage, send, startOver };
}
