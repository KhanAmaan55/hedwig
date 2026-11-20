import { useEffect, useRef, type JSX } from "react";

import { Composer } from "./Composer";
import {
  browserStorage,
  useConversation,
  type ConversationApi,
  type SessionStorage,
} from "./useConversation";
import { client } from "../../transport/client";
import { useVoice, type VoiceApi } from "../../voice/useVoice";

export interface ConversationPaneProps {
  readonly api?: ConversationApi;
  readonly storage?: SessionStorage;
  readonly voiceApi?: VoiceApi;
}

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
}: ConversationPaneProps): JSX.Element {
  const voice = useVoice(voiceApi);
  const { messages, busy, loading, error, send, startOver } = useConversation(
    api,
    storage,
    voice.say,
  );
  // Speaking over the user is the one interruption that must never happen (VOI-03).
  const sendAndHush = async (text: string) => {
    voice.stop();
    return send(text);
  };

  const listRef = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [messages, busy]);

  return (
    <section className="panel conversation" aria-label="Conversation">
      <div className="conversation__header">
        <h2 className="panel__title">Conversation</h2>
        <div className="conversation__actions">
          {voice.speaking && (
            <button className="button button--small" type="button" onClick={voice.stop}>
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
            {message.recalled !== undefined && message.recalled > 0 && (
              <p className="message__meta">
                drew on {message.recalled} {message.recalled === 1 ? "memory" : "memories"}
              </p>
            )}
          </li>
        ))}
        {busy && (
          <li className="message message--hedwig message--thinking" role="status">
            <span className="message__speaker">HEDWIG</span>
            <p className="message__text">thinking…</p>
          </li>
        )}
      </ol>

      {error !== null && (
        <p className="conversation__error" role="alert">
          {error}
        </p>
      )}

      <Composer onSend={sendAndHush} disabled={busy || loading} />
    </section>
  );
}
