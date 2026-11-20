import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../../transport/client";

export interface Loaded<T> {
  readonly data: T | null;
  readonly error: string | null;
  readonly reload: () => void;
}

/** Fetch once, and again on `reload()` — after the user changed something. */
export function useLoad<T>(load: () => Promise<T>): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    load()
      .then((value) => {
        if (!cancelled) {
          setData(value);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught instanceof ApiError ? caught.message : "Unavailable.");
      });
    return () => {
      cancelled = true;
    };
  }, [load, tick]);

  const reload = useCallback(() => {
    setTick((value) => value + 1);
  }, []);
  return { data, error, reload };
}
