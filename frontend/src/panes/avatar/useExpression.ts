/**
 * The avatar's expression intent (docs/28 §3), pushed over the live channel (EMO-02).
 *
 * With the socket up, the server sends `mind.update` the moment the expression changes — the
 * instant a turn starts "thinking", from any client — and nothing polls. Without it (no token
 * yet, backend restarting), the hook polls every two seconds as before and tries the socket
 * again shortly after. The avatar never freezes either way: it keeps its last intent and idles.
 *
 * Returns new values only when the intent actually changed, so React re-renders at most once
 * per change, not once per poll or frame.
 */

import { useEffect, useState } from "react";

import { client } from "../../transport/client";
import { stream, type MindStream } from "../../transport/socket";
import type { ExpressionIntent } from "../../transport/types";

export interface ExpressionFeed {
  readonly expression: ExpressionIntent | null;
  /** False after a failed poll while nothing is pushed. */
  readonly connected: boolean;
}

const RETRY_PUSH_MS = 10_000;

export function useExpression(intervalMs = 2000, live: MindStream | null = stream): ExpressionFeed {
  const [expression, setExpression] = useState<ExpressionIntent | null>(null);
  const [connected, setConnected] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    let last = "";
    let pushed = false;
    let stop: (() => void) | null = null;
    let retry = 0;

    const accept = (next: ExpressionIntent) => {
      const key = JSON.stringify(next);
      if (key !== last) {
        last = key;
        setExpression(next);
      }
      setConnected(true);
    };

    const poll = async () => {
      if (pushed) return;
      try {
        accept(await client.expression(controller.signal));
      } catch {
        if (!controller.signal.aborted) setConnected(false);
      }
    };

    const subscribe = () => {
      if (!live || controller.signal.aborted) return;
      live
        .watchMind(accept, () => {
          pushed = false;
          retry = window.setTimeout(subscribe, RETRY_PUSH_MS);
        })
        .then((unsubscribe) => {
          if (controller.signal.aborted) {
            unsubscribe();
            return;
          }
          stop = unsubscribe;
          pushed = true;
        })
        .catch(() => {
          retry = window.setTimeout(subscribe, RETRY_PUSH_MS);
        });
    };

    void poll();
    subscribe();
    const timer = window.setInterval(() => void poll(), intervalMs);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      window.clearTimeout(retry);
      stop?.();
    };
  }, [intervalMs, live]);

  return { expression, connected };
}
