/**
 * Why HEDWIG said that (docs/16 §14), under one reply.
 *
 * Plain words over the assembly: what it was asked, what it remembered and how much each memory
 * counted, what it looked up, how it was told to speak, its mood, and which model answered.
 * Nothing here is inferred in the browser; every line is a field of `/v1/explain`.
 */

import { useEffect, useState, type JSX } from "react";

import type { HedwigClient } from "../../transport/client";
import type { Explanation } from "../../transport/types";

export type ExplainApi = Pick<HedwigClient, "explain">;

const TRUST: Record<string, string> = {
  user: "you told me",
  self: "my own inference",
  untrusted: "read on the web",
  curated: "from your documents",
  tool: "from a tool",
};

export function WhyPanel({ api, messageId }: { api: ExplainApi; messageId: string }): JSX.Element {
  const [why, setWhy] = useState<Explanation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .explain(messageId)
      .then((value) => {
        if (!cancelled) setWhy(value);
      })
      .catch(() => {
        if (!cancelled) setError("No explanation is available for this reply.");
      });
    return () => {
      cancelled = true;
    };
  }, [api, messageId]);

  if (error) return <p className="why__line">{error}</p>;
  if (!why) return <p className="why__line">Looking it up…</p>;

  const recalled = why.retrieval?.items ?? [];
  const model = why.model.find((call) => call.purpose.startsWith("compose")) ?? why.model[0];
  return (
    <div className="why" aria-label="Why HEDWIG said this">
      <p className="why__heading">Remembered</p>
      {recalled.length === 0 ? (
        <p className="why__line">
          {why.retrieval ? "Searched memory; nothing relevant came up." : "Did not search memory."}
        </p>
      ) : (
        <ul className="why__list">
          {recalled.map((item) => (
            <li key={item.memory_id}>
              {item.text ?? "(no longer stored)"}{" "}
              <span className="why__meta">
                — {TRUST[item.trust] ?? item.trust}, relevance {item.score.toFixed(2)}
                {item.forgotten_since ? ", forgotten since" : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
      {why.tools.length > 0 && (
        <>
          <p className="why__heading">Used</p>
          <ul className="why__list">
            {why.tools.map((tool, index) => (
              <li key={`${tool.name}-${index}`}>
                {tool.name} <span className="why__meta">— {tool.status}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="why__heading">How it was told to speak</p>
      {why.directives === null ? (
        <p className="why__line">Not recorded for replies this old.</p>
      ) : (
        <ul className="why__list">
          {why.directives.map((directive) => (
            <li key={directive}>{directive}</li>
          ))}
        </ul>
      )}
      {why.rationale && <p className="why__line">Plan: {why.rationale}.</p>}
      {why.mood && (
        <p className="why__line">
          Mood at the time:{" "}
          {Object.entries(why.mood)
            .map(([name, value]) => `${name} ${Math.round(value * 100)}`)
            .join(" · ")}{" "}
          <span className="why__meta">(a simulation, not feelings)</span>
        </p>
      )}
      {model && (
        <p className="why__line why__meta">
          {model.model}, {model.tokens_in ?? "?"} tokens in, {model.tokens_out ?? "?"} out
          {model.duration_ms !== null ? `, ${(model.duration_ms / 1000).toFixed(1)} s` : ""}
        </p>
      )}
    </div>
  );
}
