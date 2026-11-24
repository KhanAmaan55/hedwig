import { useCallback, useEffect, useState } from "react";

import { client as defaultClient, type HedwigClient } from "../../transport/client";
import type { StreamApi } from "../../transport/socket";
import type { CorrectionProposal } from "../../transport/types";

/**
 * Corrections heard in this conversation, waiting for the user (docs/06 §19, MEM-07).
 *
 * "No, she lives in Lisbon" — a few seconds after the reply, HEDWIG may propose how a belief
 * should read. Nothing changes until the user says yes. Pending proposals for the conversation
 * on screen are loaded when it changes (so they survive a reload) and pushed as they are made.
 */

export type CorrectionsApi = Pick<HedwigClient, "corrections" | "settleCorrection">;

export interface Corrections {
  readonly waiting: readonly CorrectionProposal[];
  /** Settled proposals stay on screen for a moment, saying what happened. */
  readonly settled: readonly CorrectionProposal[];
  readonly settle: (proposal: CorrectionProposal, accept: boolean) => void;
  readonly error: string | null;
}

export function useCorrections(
  sessionId: string | null,
  live: StreamApi | null,
  api: CorrectionsApi = defaultClient,
): Corrections {
  const [waiting, setWaiting] = useState<readonly CorrectionProposal[]>([]);
  const [settled, setSettled] = useState<readonly CorrectionProposal[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (sessionId === null) return;
    let cancelled = false;
    api
      .corrections(sessionId)
      .then((found) => {
        if (!cancelled) setWaiting(found);
      })
      .catch(() => undefined); // a convenience: the CLI and the next reload still have them
    return () => {
      cancelled = true;
    };
  }, [api, sessionId]);

  useEffect(() => {
    const watch = live?.watchCorrections?.bind(live);
    if (!watch) return;
    return watch((proposal) => {
      if (proposal.session_id !== sessionId) return;
      setWaiting((current) =>
        current.some((p) => p.id === proposal.id) ? current : [...current, proposal],
      );
    });
  }, [live, sessionId]);

  const settle = useCallback(
    (proposal: CorrectionProposal, accept: boolean) => {
      setError(null);
      api
        .settleCorrection(proposal.id, accept)
        .then((done) => {
          setWaiting((current) => current.filter((p) => p.id !== proposal.id));
          setSettled((current) => [...current, done]);
        })
        .catch(() => {
          // Replaced or erased in the meantime: the proposal is about something gone.
          setWaiting((current) => current.filter((p) => p.id !== proposal.id));
          setError("That memory changed since; correct it from the Memory tab if needed.");
        });
    },
    [api],
  );

  const shownWaiting = sessionId === null ? [] : waiting.filter((p) => p.session_id === sessionId);
  const shownSettled = settled.filter((p) => p.session_id === sessionId);
  return { waiting: shownWaiting, settled: shownSettled, settle, error };
}
