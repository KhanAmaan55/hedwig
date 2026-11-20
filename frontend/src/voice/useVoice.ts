/**
 * The voice toggle, and the one utterance in flight (docs/15 §13).
 *
 * Off unless the user turns it on (remembered per window). When on and Piper is installed, replies
 * are spoken; otherwise the mouth moves with the text. A new utterance always stops the last.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { activityStore } from "../stores/activity";
import { client as defaultClient, type HedwigClient } from "../transport/client";

import { speakAudio, speakSilently, type Utterance } from "./player";

export type VoiceApi = Pick<HedwigClient, "speechStatus" | "speak">;

export interface Voice {
  readonly enabled: boolean;
  readonly available: boolean;
  /** Why speech is unavailable, when it is. */
  readonly reason: string;
  readonly speaking: boolean;
  readonly toggle: () => void;
  readonly say: (text: string) => void;
  readonly stop: () => void;
}

const KEY = "hedwig.voice.enabled";

function remembered(): boolean {
  try {
    return window.localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function useVoice(api: VoiceApi = defaultClient): Voice {
  const [enabled, setEnabled] = useState(remembered);
  const [available, setAvailable] = useState(false);
  const [reason, setReason] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const current = useRef<Utterance | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .speechStatus()
      .then((status) => {
        if (cancelled) return;
        setAvailable(status.available);
        setReason(status.reason);
      })
      .catch(() => {
        if (!cancelled) setReason("The backend did not answer.");
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  const stop = useCallback(() => {
    current.current?.stop();
    current.current = null;
    setSpeaking(false);
  }, []);

  const say = useCallback(
    (text: string) => {
      stop();
      const begin = (utterance: Utterance) => {
        current.current = utterance;
        setSpeaking(true);
        void utterance.done.then(() => {
          if (current.current === utterance) {
            current.current = null;
            setSpeaking(false);
          }
        });
      };
      if (enabled && available) {
        api
          .speak(text)
          .then((audio) => {
            begin(speakAudio(audio));
          })
          .catch(() => {
            begin(speakSilently(text));
          });
      } else {
        begin(speakSilently(text));
      }
    },
    [api, available, enabled, stop],
  );

  const toggle = useCallback(() => {
    setEnabled((was) => {
      try {
        window.localStorage.setItem(KEY, was ? "0" : "1");
      } catch {
        // A convenience; fine to lose.
      }
      return !was;
    });
  }, []);

  useEffect(() => stop, [stop]);
  useEffect(() => {
    if (!speaking && activityStore.get() === "speaking") activityStore.set("idle");
  }, [speaking]);

  return { enabled, available, reason, speaking, toggle, say, stop };
}
