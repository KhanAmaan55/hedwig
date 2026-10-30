/**
 * Poll the backend for expression intent (docs/28 §3).
 *
 * A poll because there is no WebSocket yet. Emotion changes on a 30-second tick, so two seconds
 * loses nothing that exists. When the live channel lands this hook reads from the stream store
 * instead, and nothing that uses it changes — the same seam as `useHealth`.
 *
 * Returns new values only when the intent actually changed, so React re-renders at most once
 * per emotion tick, not once per poll.
 */

import { useEffect, useState } from "react";

import { client } from "../../transport/client";
import type { ExpressionIntent } from "../../transport/types";

export interface ExpressionFeed {
  readonly expression: ExpressionIntent | null;
  /** False after a failed poll. The avatar keeps its last intent and idles — it never freezes. */
  readonly connected: boolean;
}

export function useExpression(intervalMs = 2000): ExpressionFeed {
  const [expression, setExpression] = useState<ExpressionIntent | null>(null);
  const [connected, setConnected] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    let last = "";

    const poll = async () => {
      try {
        const next = await client.expression(controller.signal);
        const key = JSON.stringify(next);
        if (key !== last) {
          last = key;
          setExpression(next);
        }
        setConnected(true);
      } catch {
        if (!controller.signal.aborted) setConnected(false);
      }
    };

    void poll();
    const timer = window.setInterval(() => void poll(), intervalMs);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [intervalMs]);

  return { expression, connected };
}
