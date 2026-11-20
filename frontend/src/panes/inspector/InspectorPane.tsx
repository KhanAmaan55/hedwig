/**
 * The Mind Inspector (docs/18 §7, UX-03): see inside HEDWIG, and steer it.
 *
 * Four tabs, each a window onto one module through the public API, each with the user's hand on
 * it: memory can be searched, pinned, forgotten and restored, and what the nightly run could not
 * decide alone is put to the user ("Needs your eye"); goals activated and closed;
 * findings kept or dismissed; personality and mood are shown as they are. Nothing here is
 * decorative — every number is the backend's, and every button is an API call.
 */

import { useCallback, useMemo, useState, type JSX, type KeyboardEvent } from "react";

import { ApiError, client, type HedwigClient } from "../../transport/client";
import type {
  BeliefDispute,
  DisputeChoice,
  Finding,
  Gap,
  Goal,
  MemoryItem,
} from "../../transport/types";

import { useLoad } from "./useLoad";

export type InspectorApi = Pick<
  HedwigClient,
  | "emotion"
  | "personality"
  | "relationship"
  | "memories"
  | "searchMemory"
  | "memoryAction"
  | "disputes"
  | "resolveDispute"
  | "goals"
  | "createGoal"
  | "updateGoal"
  | "gaps"
  | "askCuriosity"
  | "findings"
  | "findingAction"
>;

const TABS = ["Mind", "Memory", "Goals", "Curiosity"] as const;
type Tab = (typeof TABS)[number];

export default function InspectorPane({ api = client }: { api?: InspectorApi }): JSX.Element {
  const [tab, setTab] = useState<Tab>("Mind");

  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = TABS.indexOf(tab);
    if (event.key === "ArrowRight") setTab(TABS[(index + 1) % TABS.length] ?? "Mind");
    if (event.key === "ArrowLeft") setTab(TABS[(index + TABS.length - 1) % TABS.length] ?? "Mind");
  };

  return (
    <section className="panel inspector" aria-label="Mind Inspector">
      <h2 className="panel__title">Mind Inspector</h2>
      <div className="tabs" role="tablist" aria-label="Inspector sections" onKeyDown={onKey}>
        {TABS.map((name) => (
          <button
            key={name}
            role="tab"
            type="button"
            className="tabs__tab"
            aria-selected={tab === name}
            tabIndex={tab === name ? 0 : -1}
            onClick={() => {
              setTab(name);
            }}
          >
            {name}
          </button>
        ))}
      </div>
      <div role="tabpanel" aria-label={tab} className="inspector__body">
        {tab === "Mind" && <MindTab api={api} />}
        {tab === "Memory" && <MemoryTab api={api} />}
        {tab === "Goals" && <GoalsTab api={api} />}
        {tab === "Curiosity" && <CuriosityTab api={api} />}
      </div>
    </section>
  );
}

function Bar({ label, value }: { label: string; value: number }): JSX.Element {
  const percent = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="bar">
      <span className="bar__label">{label}</span>
      <span
        className="bar__track"
        role="meter"
        aria-label={label}
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <span className="bar__fill" style={{ width: `${percent}%` }} />
      </span>
      <span className="bar__value">{percent}</span>
    </div>
  );
}

function MindTab({ api }: { api: InspectorApi }): JSX.Element {
  const emotion = useLoad(useCallback(() => api.emotion(), [api]));
  const relationship = useLoad(useCallback(() => api.relationship(), [api]));
  const personality = useLoad(useCallback(() => api.personality(), [api]));
  return (
    <>
      <h3 className="inspector__heading">Mood</h3>
      {emotion.data ? (
        (["happiness", "curiosity", "confidence", "trust", "energy", "stress"] as const).map(
          (name) => <Bar key={name} label={name} value={emotion.data?.[name] ?? 0} />,
        )
      ) : (
        <p className="panel__hint">{emotion.error ?? "Loading…"}</p>
      )}
      <p className="panel__hint">A simulation that shapes how HEDWIG speaks — not feelings.</p>
      <h3 className="inspector__heading">How well HEDWIG knows you</h3>
      {relationship.data ? (
        <>
          <Bar label="familiarity" value={relationship.data.familiarity} />
          <Bar label="trust in your claims" value={relationship.data.trust} />
          <p className="panel__hint">{relationship.data.interaction_count} turns together.</p>
        </>
      ) : (
        <p className="panel__hint">{relationship.error ?? "Loading…"}</p>
      )}
      <h3 className="inspector__heading">Personality</h3>
      {personality.data && Array.isArray(personality.data.traits) ? (
        personality.data.traits.map((trait) => (
          <Bar key={trait.trait} label={trait.trait} value={trait.value} />
        ))
      ) : (
        <p className="panel__hint">{personality.error ?? "Loading…"}</p>
      )}
    </>
  );
}

function MemoryTab({ api }: { api: InspectorApi }): JSX.Element {
  const [showForgotten, setShowForgotten] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MemoryItem[] | null>(null);
  const list = useLoad(useCallback(() => api.memories(showForgotten), [api, showForgotten]));

  const act = async (memory: MemoryItem, action: "pin" | "unpin" | "forget" | "restore") => {
    await api.memoryAction(memory.id, action);
    list.reload();
    if (results) setResults((await api.searchMemory(query)).hits.map((hit) => hit.memory));
  };
  const search = async () => {
    setResults(
      query.trim() ? (await api.searchMemory(query.trim())).hits.map((h) => h.memory) : null,
    );
  };
  const shown = results ?? (Array.isArray(list.data) ? list.data : []);

  return (
    <>
      <Disputes
        api={api}
        onSettled={() => {
          list.reload();
        }}
      />
      <form
        className="inspector__form"
        onSubmit={(event) => {
          event.preventDefault();
          void search();
        }}
      >
        <input
          aria-label="Search memory"
          className="inspector__input"
          placeholder="Search as HEDWIG recalls…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
          }}
        />
        <button className="button button--small" type="submit">
          Search
        </button>
      </form>
      <label className="panel__hint">
        <input
          type="checkbox"
          checked={showForgotten}
          onChange={(event) => {
            setShowForgotten(event.target.checked);
          }}
        />{" "}
        show forgotten
      </label>
      {list.error && <p className="panel__hint">{list.error}</p>}
      {shown.length === 0 && !list.error && <p className="panel__hint">Nothing remembered yet.</p>}
      <ul className="items">
        {shown.map((memory) => (
          <li key={memory.id} className={`item${memory.forgotten ? " item--muted" : ""}`}>
            <p className="item__text">{memory.text}</p>
            <p className="item__meta">
              {memory.kind} ·{" "}
              {memory.trust === "untrusted" ? "read on the web" : `trust: ${memory.trust}`}
              {memory.pinned && " · pinned"}
              {memory.forgotten && " · forgotten"}
              {memory.status === "superseded" && " · no longer true"}
            </p>
            <div className="item__actions">
              {memory.forgotten ? (
                <button
                  className="button button--small"
                  type="button"
                  onClick={() => void act(memory, "restore")}
                >
                  Restore
                </button>
              ) : (
                <>
                  <button
                    className="button button--small"
                    type="button"
                    onClick={() => void act(memory, memory.pinned ? "unpin" : "pin")}
                  >
                    {memory.pinned ? "Unpin" : "Pin"}
                  </button>
                  {!memory.pinned && (
                    <button
                      className="button button--small"
                      type="button"
                      onClick={() => void act(memory, "forget")}
                    >
                      Forget
                    </button>
                  )}
                </>
              )}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

const CHOICES: Record<BeliefDispute["verdict"], { choice: DisputeChoice; label: string }[]> = {
  contradicts: [
    { choice: "first", label: "First is true" },
    { choice: "second", label: "Second is true" },
    { choice: "both", label: "Both are true" },
  ],
  same: [
    { choice: "same", label: "Same thing" },
    { choice: "both", label: "Not the same" },
  ],
};

/**
 * What the nightly run could not decide alone (docs/12 §15). A small model's "these conflict" is
 * a guess, so it is shown here rather than acted on; the user's answer is what changes memory.
 */
function Disputes({ api, onSettled }: { api: InspectorApi; onSettled: () => void }): JSX.Element {
  const disputes = useLoad(useCallback(() => api.disputes(), [api]));
  const [problem, setProblem] = useState<string | null>(null);
  const open = Array.isArray(disputes.data) ? disputes.data : [];

  const settle = async (dispute: BeliefDispute, choice: DisputeChoice) => {
    try {
      await api.resolveDispute(dispute.pair, choice);
      setProblem(null);
    } catch (caught: unknown) {
      setProblem(caught instanceof ApiError ? caught.message : "That did not work.");
    }
    disputes.reload();
    onSettled();
  };

  if (open.length === 0) return <></>;
  return (
    <section aria-label="Needs your eye" className="disputes">
      <h3 className="inspector__heading">Needs your eye</h3>
      {problem && (
        <p className="panel__hint" role="alert">
          {problem}
        </p>
      )}
      <ul className="items">
        {open.map((dispute) => (
          <li key={dispute.pair} className="item">
            <p className="item__meta">
              {dispute.verdict === "contradicts"
                ? "These may not both be true:"
                : "These may say the same thing:"}
            </p>
            <p className="item__text">1. {dispute.first.statement}</p>
            <p className="item__text">2. {dispute.second.statement}</p>
            <div className="item__actions">
              {CHOICES[dispute.verdict].map(({ choice, label }) => (
                <button
                  key={choice}
                  className="button button--small"
                  type="button"
                  onClick={() => void settle(dispute, choice)}
                >
                  {label}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function GoalsTab({ api }: { api: InspectorApi }): JSX.Element {
  const goals = useLoad(useCallback(() => api.goals(), [api]));
  const [draft, setDraft] = useState("");
  const set = async (goal: Goal, changes: Partial<Pick<Goal, "status" | "progress">>) => {
    await api.updateGoal(goal.id, changes);
    goals.reload();
  };
  return (
    <>
      <form
        className="inspector__form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!draft.trim()) return;
          void api.createGoal(draft.trim()).then(() => {
            setDraft("");
            goals.reload();
          });
        }}
      >
        <input
          aria-label="New goal"
          className="inspector__input"
          placeholder="Something to work on together…"
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
          }}
        />
        <button className="button button--small" type="submit">
          Add
        </button>
      </form>
      {goals.error && <p className="panel__hint">{goals.error}</p>}
      {Array.isArray(goals.data) && goals.data.length === 0 && (
        <p className="panel__hint">No goals yet.</p>
      )}
      <ul className="items">
        {(Array.isArray(goals.data) ? goals.data : []).map((goal) => (
          <li key={goal.id} className="item">
            <p className="item__text">{goal.description}</p>
            <Bar label={goal.status} value={goal.progress} />
            {goal.origin === "reflection" && goal.status === "proposed" && (
              <p className="item__meta">Proposed by HEDWIG from a conversation.</p>
            )}
            <div className="item__actions">
              {goal.status !== "active" && (
                <button
                  className="button button--small"
                  type="button"
                  onClick={() => void set(goal, { status: "active" })}
                >
                  Activate
                </button>
              )}
              {goal.status !== "proposed" && (
                <button
                  className="button button--small"
                  type="button"
                  onClick={() => void set(goal, { status: "done", progress: 1 })}
                >
                  Done
                </button>
              )}
              <button
                className="button button--small"
                type="button"
                onClick={() => void set(goal, { status: "abandoned" })}
              >
                Drop
              </button>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

function CuriosityTab({ api }: { api: InspectorApi }): JSX.Element {
  const findings = useLoad(useCallback(() => api.findings(), [api]));
  const gaps = useLoad(useCallback(() => api.gaps(), [api]));
  const [question, setQuestion] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const act = async (finding: Finding, action: "confirm" | "dismiss") => {
    await api.findingAction(finding.id, action);
    findings.reload();
  };
  const open = useMemo<Gap[]>(() => (Array.isArray(gaps.data) ? gaps.data : []), [gaps.data]);
  const found = Array.isArray(findings.data) ? findings.data : [];
  return (
    <>
      <h3 className="inspector__heading">Found while exploring</h3>
      <p className="panel__hint">Read on the web: untrusted until you keep it.</p>
      {found.length === 0 && <p className="panel__hint">Nothing yet.</p>}
      <ul className="items">
        {found.map((finding) => (
          <li key={finding.id} className="item">
            {finding.question && <p className="item__meta">{finding.question}</p>}
            <p className="item__text">{finding.summary}</p>
            <ul className="item__sources">
              {finding.sources.slice(0, 3).map((source) => (
                <li key={source.url}>
                  <a href={source.url} target="_blank" rel="noreferrer noopener">
                    {source.title ?? source.url}
                  </a>
                </li>
              ))}
            </ul>
            {finding.status !== "confirmed" && (
              <div className="item__actions">
                <button
                  className="button button--small"
                  type="button"
                  onClick={() => void act(finding, "confirm")}
                >
                  Keep
                </button>
                <button
                  className="button button--small"
                  type="button"
                  onClick={() => void act(finding, "dismiss")}
                >
                  Not interested
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
      <h3 className="inspector__heading">Open questions</h3>
      <form
        className="inspector__form"
        onSubmit={(event) => {
          event.preventDefault();
          void api
            .askCuriosity(question)
            .then(() => {
              setQuestion("");
              setNote(null);
              gaps.reload();
            })
            .catch((caught: unknown) => {
              setNote(caught instanceof Error ? caught.message : "Could not add that.");
            });
        }}
      >
        <input
          aria-label="Something to find out"
          className="inspector__input"
          placeholder="Something to find out, in general terms…"
          value={question}
          onChange={(event) => {
            setQuestion(event.target.value);
          }}
        />
        <button
          className="button button--small"
          type="submit"
          disabled={question.trim().length < 8}
        >
          Ask
        </button>
      </form>
      {note && (
        <p className="panel__hint" role="alert">
          {note}
        </p>
      )}
      <ul className="items">
        {open.map((gap) => (
          <li key={gap.id} className="item">
            <p className="item__text">{gap.question}</p>
            <p className="item__meta">{gap.origin.replace("_", " ")}</p>
          </li>
        ))}
      </ul>
    </>
  );
}
