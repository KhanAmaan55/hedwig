import { useEffect, useRef, useState, type JSX } from "react";

import { Composer } from "./Composer";
import { useCorrections, type CorrectionsApi } from "./useCorrections";
import {
  browserStorage,
  useConversation,
  type ConversationApi,
  type SessionStorage,
} from "./useConversation";
import { client, type HedwigClient } from "../../transport/client";
import type { StreamApi } from "../../transport/socket";
import { useMicrophone, type ListenApi } from "../../voice/useMicrophone";
import { useVoice, type SpokenReply, type VoiceApi } from "../../voice/useVoice";

import { WhyPanel, type ExplainApi } from "./WhyPanel";

export interface ConversationPaneProps {
  readonly api?: ConversationApi;
  readonly storage?: SessionStorage;
  readonly voiceApi?: VoiceApi;
  /** Speech input (VOI-02). */
  readonly listenApi?: ListenApi;
  readonly explainApi?: ExplainApi;
  /** The live channel (docs/16 §13). Without it, replies arrive whole over REST. */
  readonly live?: StreamApi | null;
  /** *Not interested* on what HEDWIG said unprompted (docs/11 §14). */
  readonly initiativeApi?: Pick<HedwigClient, "dismissOffer">;
  /** Corrections heard in conversation, for the user to confirm (docs/06 §19). */
  readonly correctionsApi?: CorrectionsApi;
}

const STAGES: Record<string, string> = {
  recalling: "remembering…",
  deliberating: "thinking…",
  acting: "checking…",
  composing: "putting it into words…",
};

/**
 * Talk to HEDWIG (docs/18 §5, §13).
 *
 * Replies are rendered as text, never as HTML: React escapes them, and `white-space: pre-wrap`
 * keeps the model's line breaks. Sanitised Markdown is a deliberate later step.
 */
export default function ConversationPane({
  api = client,
  storage = browserStorage,
  voiceApi = client,
  listenApi = client,
  explainApi = client,
  live = null,
  initiativeApi = client,
  correctionsApi = client,
}: ConversationPaneProps): JSX.Element {
  const voice = useVoice(voiceApi);
  const microphone = useMicrophone(listenApi);
  // The reply being spoken sentence by sentence as it streams (docs/15 §14), if the voice is on.
  const spoken = useRef<SpokenReply | null>(null);
  const opened = useRef(false);
  const { sessionId, messages, busy, loading, error, stage, send, stop, startOver } =
    useConversation(
      api,
      storage,
      (text, streamed) => {
        const reply = spoken.current;
        spoken.current = null;
        if (reply) reply.finish(text);
        else voice.say(text, { fallback: !streamed });
      },
      live,
      (piece) => {
        if (!opened.current) {
          opened.current = true;
          spoken.current = voice.streamReply();
        }
        spoken.current?.feed(piece);
      },
      () => {
        // No reply will finish what was being spoken: stop it, or the face keeps talking.
        if (spoken.current || opened.current) voice.stop();
        spoken.current = null;
        opened.current = false;
      },
    );
  // Speaking over the user is the one interruption that must never happen (VOI-03).
  const sendAndHush = async (text: string) => {
    voice.stop();
    spoken.current = null;
    opened.current = false;
    return send(text);
  };

  const corrections = useCorrections(sessionId, live, correctionsApi);
  const [why, setWhy] = useState<string | null>(null);
  const [waved, setWaved] = useState<ReadonlySet<string>>(new Set());
  const notInterested = (offerId: string) => {
    setWaved((current) => new Set(current).add(offerId));
    void initiativeApi.dismissOffer(offerId).catch(() => {
      setWaved((current) => {
        const next = new Set(current);
        next.delete(offerId);
        return next;
      });
    });
  };
  const listRef = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [messages, busy, corrections.waiting.length]);

  return (
    <section className="panel conversation" aria-label="Conversation">
      <div className="conversation__header">
        <h2 className="panel__title">Conversation</h2>
        <div className="conversation__actions">
          {(voice.speaking || (busy && stop)) && (
            <button
              className="button button--small"
              type="button"
              onClick={() => {
                voice.stop();
                stop?.();
              }}
            >
              Stop
            </button>
          )}
          <button
            className="button button--small"
            type="button"
            aria-pressed={voice.enabled}
            onClick={voice.toggle}
            disabled={!voice.available && !voice.enabled}
            title={voice.available ? "Speak replies aloud" : voice.reason || "No voice installed"}
          >
            {voice.enabled ? "Voice on" : "Voice off"}
          </button>
          <button
            className="button button--small"
            type="button"
            onClick={startOver}
            disabled={busy || messages.length === 0}
          >
            New conversation
          </button>
        </div>
      </div>

      <ol className="conversation__messages" ref={listRef} aria-live="polite">
        {loading && <li className="conversation__empty">Picking up where we left off…</li>}
        {!loading && messages.length === 0 && (
          <li className="conversation__empty">Nothing said yet. Say hello.</li>
        )}
        {messages.map((message) => (
          <li
            key={message.key}
            className={`message message--${message.role}${message.failed ? " message--failed" : ""}`}
          >
            <span className="message__speaker">{message.role === "user" ? "You" : "HEDWIG"}</span>
            <p className="message__text">{message.text}</p>
            {message.offerId !== undefined && (
              <p className="message__meta">
                HEDWIG started this
                {waved.has(message.offerId) ? (
                  " · noted, it won't bring this up again"
                ) : (
                  <>
                    {" · "}
                    <button
                      className="message__why"
                      type="button"
                      onClick={() => {
                        if (message.offerId) notInterested(message.offerId);
                      }}
                    >
                      Not interested
                    </button>
                  </>
                )}
              </p>
            )}
            {message.recalled !== undefined && message.recalled > 0 && (
              <p className="message__meta">
                drew on {message.recalled} {message.recalled === 1 ? "memory" : "memories"}
              </p>
            )}
            {message.role === "hedwig" && message.key.startsWith("msg_") && !message.offerId && (
              <>
                <button
                  className="message__why"
                  type="button"
                  aria-expanded={why === message.key}
                  onClick={() => {
                    setWhy(why === message.key ? null : message.key);
                  }}
                >
                  Why?
                </button>
                {why === message.key && <WhyPanel api={explainApi} messageId={message.key} />}
              </>
            )}
          </li>
        ))}
        {corrections.waiting.map((proposal) => (
          <li key={proposal.id} className="message message--hedwig correction" role="group">
            <span className="message__speaker">Did I get this wrong?</span>
            <p className="message__text">
              You said this is wrong: “{proposal.current_text}”
              <br />
              Remember instead: “{proposal.proposed_text}”
            </p>
            <p className="message__meta">
              <button
                className="button button--small"
                type="button"
                onClick={() => {
                  corrections.settle(proposal, true);
                }}
              >
                Yes, correct it
              </button>{" "}
              <button
                className="button button--small"
                type="button"
                onClick={() => {
                  corrections.settle(proposal, false);
                }}
              >
                No
              </button>
            </p>
          </li>
        ))}
        {corrections.settled.map((proposal) => (
          <li key={proposal.id} className="message message--hedwig correction" role="status">
            <p className="message__meta">
              {proposal.status === "accepted"
                ? `Corrected: “${proposal.proposed_text}”`
                : "Left as it was."}
            </p>
          </li>
        ))}
        {busy && !messages.some((m) => m.streaming) && (
          <li className="message message--hedwig message--thinking" role="status">
            <span className="message__speaker">HEDWIG</span>
            <p className="message__text">{(stage && STAGES[stage]) ?? "thinking…"}</p>
          </li>
        )}
      </ol>

      {(error ?? corrections.error) !== null && (
        <p className="conversation__error" role="alert">
          {error ?? corrections.error}
        </p>
      )}

      <Composer onSend={sendAndHush} disabled={busy || loading} microphone={microphone} />
    </section>
  );
}
