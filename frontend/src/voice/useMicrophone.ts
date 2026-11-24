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
export type MicState = "idle" | "starting" | "recording" | "transcribing";

export interface Microphone {
  readonly available: boolean;
  readonly reason: string;
  readonly state: MicState;
  readonly error: string | null;
  /** Start recording, or stop and transcribe. Resolves with the text when stopping. */
  readonly toggle: () => Promise<string | null>;
  /** Start recording, unless already recording or starting. Resolves whether it is recording. */
  readonly start: () => Promise<boolean>;
  /**
   * Stop and transcribe. A stop that arrives while the microphone is still starting is kept and
   * applied the moment it starts, so a quick press-and-release never leaves one recording.
   */
  readonly stop: () => Promise<string | null>;
}

export function useMicrophone(api: ListenApi): Microphone {
  const [available, setAvailable] = useState(false);
  const [reason, setReason] = useState("");
  const [state, setState] = useState<MicState>("idle");
  const [error, setError] = useState<string | null>(null);
  // The truth for decisions: refs change at once, React state on the next render.
  const phase = useRef<MicState>("idle");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const stopWhenStarted = useRef<((heard: string | null) => void) | null>(null);

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

  const finish = useCallback(
    async (active: MediaRecorder): Promise<string | null> => {
      const stopped = new Promise<Blob>((resolve) => {
        active.onstop = () => {
          resolve(new Blob(chunks.current, { type: active.mimeType || "audio/webm" }));
        };
      });
      active.stop();
      for (const track of active.stream.getTracks()) track.stop();
      recorder.current = null;
      phase.current = "transcribing";
      setState("transcribing");
      try {
        const heard = await api.transcribe(await stopped);
        return heard.text;
      } catch (caught: unknown) {
        setError(caught instanceof ApiError ? caught.message : "HEDWIG could not hear that.");
        return null;
      } finally {
        phase.current = "idle";
        setState("idle");
      }
    },
    [api],
  );

  const start = useCallback(async (): Promise<boolean> => {
    if (phase.current !== "idle") return phase.current === "recording";
    phase.current = "starting";
    setState("starting");
    setError(null);
    let started: MediaRecorder;
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      started = new MediaRecorder(media);
    } catch {
      phase.current = "idle";
      setState("idle");
      setError("The microphone is not available.");
      const waiting = stopWhenStarted.current;
      stopWhenStarted.current = null;
      waiting?.(null);
      return false;
    }
    chunks.current = [];
    started.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.current.push(event.data);
    };
    started.start();
    recorder.current = started;
    phase.current = "recording";
    setState("recording");
    const waiting = stopWhenStarted.current;
    if (waiting) {
      stopWhenStarted.current = null;
      void finish(started).then(waiting);
    }
    return true;
  }, [finish]);

  const stop = useCallback(async (): Promise<string | null> => {
    if (phase.current === "starting") {
      return new Promise((resolve) => {
        stopWhenStarted.current = resolve;
      });
    }
    if (phase.current !== "recording" || !recorder.current) return null;
    return finish(recorder.current);
  }, [finish]);

  const toggle = useCallback(async (): Promise<string | null> => {
    if (phase.current === "starting" || phase.current === "recording") return stop();
    if (phase.current === "idle") await start();
    return null;
  }, [start, stop]);

  return { available, reason, state, error, toggle, start, stop };
}
