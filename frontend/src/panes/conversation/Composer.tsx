import { useState, type JSX, type KeyboardEvent, type SyntheticEvent } from "react";

import type { Microphone } from "../../voice/useMicrophone";

/** docs/16 §7: message text caps at 32 KB. */
export const MAX_MESSAGE_CHARS = 32_768;

export interface ComposerProps {
  /** Resolves false when nothing was sent; the text is then given back. */
  readonly onSend: (text: string) => Promise<boolean>;
  readonly disabled: boolean;
  /** Speech input (VOI-02). The transcript is added to the box for the user to send. */
  readonly microphone?: Microphone | null;
}

/** Enter sends, Shift+Enter is a new line. Text is cleared at once and restored on failure. */
export function Composer({ onSend, disabled, microphone = null }: ComposerProps): JSX.Element {
  const [text, setText] = useState("");

  const submit = async () => {
    if (disabled || !text.trim()) return;
    const sent = text;
    setText("");
    if (!(await onSend(sent))) setText(sent);
  };

  const onSubmit = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submit();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // Not while an input method is composing: Enter there confirms a character.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  };

  return (
    <form className="composer" onSubmit={onSubmit}>
      <label className="visually-hidden" htmlFor="composer-text">
        Message HEDWIG
      </label>
      <textarea
        id="composer-text"
        className="composer__input"
        rows={2}
        maxLength={MAX_MESSAGE_CHARS}
        placeholder="Say something to HEDWIG…"
        value={text}
        onChange={(event) => {
          setText(event.target.value);
        }}
        onKeyDown={onKeyDown}
      />
      {microphone?.available && (
        <button
          className="button"
          type="button"
          aria-pressed={microphone.state === "recording"}
          disabled={microphone.state === "transcribing"}
          title="Talk instead of typing; what you say appears here to send"
          onClick={() => {
            void microphone.toggle().then((heard) => {
              if (heard) setText((current) => (current ? `${current} ${heard}` : heard));
            });
          }}
        >
          {microphone.state === "recording"
            ? "Stop"
            : microphone.state === "transcribing"
              ? "Hearing…"
              : "Mic"}
        </button>
      )}
      <button className="button" type="submit" disabled={disabled || !text.trim()}>
        Send
      </button>
      {microphone?.error && (
        <p className="panel__hint" role="alert">
          {microphone.error}
        </p>
      )}
    </form>
  );
}
