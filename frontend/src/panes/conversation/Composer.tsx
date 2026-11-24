import {
  useRef,
  useState,
  type JSX,
  type KeyboardEvent,
  type PointerEvent,
  type SyntheticEvent,
} from "react";

import type { Microphone } from "../../voice/useMicrophone";

/** docs/16 §7: message text caps at 32 KB. */
export const MAX_MESSAGE_CHARS = 32_768;

/** Held this long, the Mic button is push-to-talk: release sends what was said (docs/15 §14). */
export const HOLD_MS = 400;

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
  // Push-to-talk: when the press began, and whether that press started the recording. Whether
  // the microphone is recording is the microphone's to say, never assumed from a press: a start
  // that failed (permission refused) must not turn the next press into a stop.
  const press = useRef<{ at: number; started: boolean } | null>(null);

  const addHeard = (heard: string | null) => {
    if (heard) setText((current) => (current ? `${current} ${heard}` : heard));
  };

  /** Held and released: send what was heard; if it could not be sent, keep it in the box. */
  const sendHeard = async (heard: string) => {
    if (disabled || !(await onSend(heard))) addHeard(heard);
  };

  const onPointerDown = (event: PointerEvent<HTMLButtonElement>) => {
    if (!microphone) return;
    try {
      // Released outside the button, the release still comes here (docs/15 §14).
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // No pointer capture (an old browser, a test DOM): the release over the button still works.
    }
    const idle = microphone.state === "idle";
    press.current = { at: performance.now(), started: idle };
    if (idle) void microphone.start();
  };

  const onPointerUp = () => {
    const pressed = press.current;
    press.current = null;
    if (!pressed || !microphone) return;
    const held = performance.now() - pressed.at >= HOLD_MS;
    if (pressed.started && !held) return; // a click: keep listening until the next one
    void microphone.stop().then((heard) => {
      if (pressed.started && heard) void sendHeard(heard);
      else addHeard(heard);
    });
  };

  const onPointerCancel = () => {
    // The browser took the gesture (a scroll, a system dialog): stop, and send nothing.
    const pressed = press.current;
    press.current = null;
    if (pressed?.started) void microphone?.stop();
  };

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
          aria-pressed={microphone.state === "recording" || microphone.state === "starting"}
          disabled={microphone.state === "transcribing"}
          title="Click to talk and see what you said before sending; hold to talk and send on release"
          onPointerDown={onPointerDown}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerCancel}
          onClick={(event) => {
            // The keyboard (Enter or Space on the button) has no pointer: toggle as before.
            if (event.detail !== 0) return;
            void microphone.toggle().then(addHeard);
          }}
        >
          {microphone.state === "recording" || microphone.state === "starting"
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
