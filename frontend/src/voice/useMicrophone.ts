/**
 * Talking to HEDWIG (VOI-02): record, transcribe on this machine, hand the text back.
 *
 * The text goes into the message box, not straight to HEDWIG: speech recognition makes mistakes,
 * and the user should see what will be sent. The recording itself is never stored — it is posted
 * once to the local backend, transcribed, and dropped.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, type HedwigClient } from "../transport/client";

export type ListenApi = Pick<HedwigClient, "speechStatus" | "transcribe">;
export type MicState = "idle" | "recording" | "transcribing";

export interface Microphone {
  readonly available: boolean;
  readonly reason: string;
  readonly state: MicState;
  readonly error: string | null;
  /** Start recording, or stop and transcribe. Resolves with the text when stopping. */
  readonly toggle: () => Promise<string | null>;
}

export function useMicrophone(api: ListenApi): Microphone {
  const [available, setAvailable] = useState(false);
  const [reason, setReason] = useState("");
  const [state, setState] = useState<MicState>("idle");
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .speechStatus()
      .then((status) => {
        if (cancelled) return;
        setAvailable(Boolean(status.listening));
        setReason(status.listening_reason ?? "");
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [api]);

  const toggle = useCallback(async (): Promise<string | null> => {
    setError(null);
    if (state === "recording" && recorder.current) {
      const active = recorder.current;
      const stopped = new Promise<Blob>((resolve) => {
        active.onstop = () => {
          resolve(new Blob(chunks.current, { type: active.mimeType || "audio/webm" }));
        };
      });
      active.stop();
      for (const track of active.stream.getTracks()) track.stop();
      recorder.current = null;
      setState("transcribing");
      try {
        const heard = await api.transcribe(await stopped);
        return heard.text;
      } catch (caught: unknown) {
        setError(caught instanceof ApiError ? caught.message : "HEDWIG could not hear that.");
        return null;
      } finally {
        setState("idle");
      }
    }
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      const started = new MediaRecorder(media);
      chunks.current = [];
      started.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.current.push(event.data);
      };
      started.start();
      recorder.current = started;
      setState("recording");
    } catch {
      setError("The microphone is not available.");
    }
    return null;
  }, [api, state]);

  return { available, reason, state, error, toggle };
}
