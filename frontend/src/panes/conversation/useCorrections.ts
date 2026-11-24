import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, client as defaultClient, type HedwigClient } from "../../transport/client";
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
  // Pushed while a load was in flight, and settled here: what a load's snapshot may not know.
  const pushed = useRef(new Set<string>());
  const done = useRef(new Set<string>());

  useEffect(() => {
    if (sessionId === null) return;
    let cancelled = false;
    pushed.current = new Set();
    api
      .corrections(sessionId)
      .then((found) => {
        if (cancelled) return;
        // The snapshot may predate a proposal pushed meanwhile, or one settled meanwhile.
        setWaiting((current) => {
          const listed = new Set(found.map((p) => p.id));
          const newer = current.filter((p) => pushed.current.has(p.id) && !listed.has(p.id));
          return [...found, ...newer].filter((p) => !done.current.has(p.id));
        });
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
      pushed.current.add(proposal.id);
      setWaiting((current) =>
        // An answered one stays answered, however late its own notice arrives.
        done.current.has(proposal.id) || current.some((p) => p.id === proposal.id)
          ? current
          : [...current, proposal],
      );
    });
  }, [live, sessionId]);

  const settle = useCallback(
    (proposal: CorrectionProposal, accept: boolean) => {
      setError(null);
      const gone = () => {
        done.current.add(proposal.id);
        setWaiting((current) => current.filter((p) => p.id !== proposal.id));
      };
      api
        .settleCorrection(proposal.id, accept)
        .then((result) => {
          gone();
          setSettled((current) => [...current, result]);
        })
        .catch((caught: unknown) => {
          if (
            caught instanceof ApiError &&
            (caught.code === "conflict" || caught.code === "not_found")
          ) {
            // Replaced, erased or already answered: the proposal is about something gone.
            gone();
            setError("That memory changed since; correct it from the Memory tab if needed.");
            return;
          }
          // Not reached, or not answered: nothing changed, so the question stays to answer.
          setError("HEDWIG did not get that answer. Try again.");
        });
    },
    [api],
  );

  const shownWaiting = sessionId === null ? [] : waiting.filter((p) => p.session_id === sessionId);
  const shownSettled = settled.filter((p) => p.session_id === sessionId);
  return { waiting: shownWaiting, settled: shownSettled, settle, error };
}
