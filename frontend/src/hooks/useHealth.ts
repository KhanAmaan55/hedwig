import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, client } from "../transport/client";
import type { Health } from "../transport/types";

export type ConnectionState = "connecting" | "connected" | "disconnected";

export interface HealthResult {
  readonly state: ConnectionState;
  readonly health: Health | null;
  readonly error: string | null;
  readonly latencyMs: number | null;
  readonly refresh: () => void;
}

/**
 * Poll `/v1/health`.
 *
 * A poll rather than a subscription because Milestone 1 has no WebSocket yet. When the
 * live channel lands (docs/16 §5) this hook reads from the stream store instead, and
 * nothing above it changes.
 */
export function useHealth(intervalMs = 5000): HealthResult {
  const [health, setHealth] = useState<Health | null>(null);
  const [state, setState] = useState<ConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const [tick, setTick] = useState(0);

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(() => {
    setTick((value) => value + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    const poll = async () => {
      const started = performance.now();
      try {
        const result = await client.health(controller.signal);
        if (!mounted.current) return;
        setHealth(result);
        setLatencyMs(Math.round(performance.now() - started));
        setState("connected");
        setError(null);
      } catch (caught) {
        if (!mounted.current || controller.signal.aborted) return;
        setState("disconnected");
        setLatencyMs(null);
        setError(
          caught instanceof ApiError ? `${caught.code}: ${caught.message}` : "Backend unreachable.",
        );
      }
    };

    void poll();
    const timer = window.setInterval(() => void poll(), intervalMs);

    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [intervalMs, tick]);

  return { state, health, error, latencyMs, refresh };
}
