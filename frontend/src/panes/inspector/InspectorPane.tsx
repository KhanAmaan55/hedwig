/**
 * The Mind Inspector (docs/18 §7, UX-03): see inside HEDWIG, and steer it.
 *
 * Four tabs, each a window onto one module through the public API, each with the user's hand on
 * it: memory can be searched, pinned, forgotten and restored, and what the nightly run could not
 * decide alone is put to the user ("Needs your eye"); goals activated and closed;
 * findings kept or dismissed; everything can be downloaded, and anything erased for good (Data);
 * personality and mood are shown as they are. Nothing here is
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
  PersonalityChange,
  PurgePreview,
  PurgeReceipt,
} from "../../transport/types";

import { useLoad } from "./useLoad";

export type InspectorApi = Pick<
  HedwigClient,
  | "emotion"
  | "personality"
  | "personalityHistory"
  | "revertChange"
  | "relationship"
  | "memories"
  | "searchMemory"
  | "memoryAction"
  | "correctMemory"
  | "disputes"
  | "resolveDispute"
  | "exportAll"
  | "purgePreview"
  | "purge"
  | "goals"
  | "createGoal"
  | "updateGoal"
  | "gaps"
  | "askCuriosity"
  | "findings"
  | "findingAction"
>;

const TABS = ["Mind", "Memory", "Goals", "Curiosity", "Data"] as const;
type Tab = (typeof TABS)[number];

/** Hand bytes to the user as a file. Injected so tests need no browser download machinery. */
export type SaveFile = (data: ArrayBuffer, name: string) => void;

const saveInBrowser: SaveFile = (data, name) => {
  const url = URL.createObjectURL(new Blob([data], { type: "application/zip" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
};

export default function InspectorPane({
  api = client,
  save = saveInBrowser,
}: {
  api?: InspectorApi;
  save?: SaveFile;
}): JSX.Element {
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
        {tab === "Data" && <DataTab api={api} save={save} />}
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
      <Changes api={api} onChange={personality.reload} />
    </>
  );
}

/**
 * How HEDWIG has changed, and why — each change undoable (docs/10 §5.5, §13). Drift that cannot be
 * seen or undone would be a one-way risk; this is where the user sees it.
 */
function Changes({ api, onChange }: { api: InspectorApi; onChange: () => void }): JSX.Element {
  const changes = useLoad(useCallback(() => api.personalityHistory(), [api]));
  const shown: PersonalityChange[] = Array.isArray(changes.data) ? changes.data : [];
  const revert = async (change: PersonalityChange) => {
    await api.revertChange(change.id);
    changes.reload();
    onChange();
  };
  if (shown.length === 0) return <></>;
  return (
    <section aria-label="Recent changes">
      <h3 className="inspector__heading">Recent changes</h3>
      <ul className="items">
        {shown.map((change) => (
          <li key={change.id} className={`item${change.reverted_at ? " item--muted" : ""}`}>
            <p className="item__text">
              {change.trait} {change.delta > 0 ? "+" : "−"}
              {Math.abs(change.delta).toFixed(3)}
            </p>
            <p className="item__meta">
              {change.applied_at.slice(0, 10)} · {SOURCES[change.source] ?? change.source}
              {change.rationale ? ` · ${change.rationale}` : ""}
              {change.reverted_at ? " · undone" : ""}
            </p>
            {!change.reverted_at && change.source !== "revert" && (
              <div className="item__actions">
                <button
                  className="button button--small"
                  type="button"
                  onClick={() => void revert(change)}
                >
                  Revert
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

const SOURCES: Record<string, string> = {
  instruction: "you asked",
  inferred: "from your reactions",
  reset: "reset",
  revert: "undo",
};

function MemoryTab({ api }: { api: InspectorApi }): JSX.Element {
  const [showForgotten, setShowForgotten] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MemoryItem[] | null>(null);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const list = useLoad(useCallback(() => api.memories(showForgotten), [api, showForgotten]));

  const saveCorrection = async () => {
    if (!editing) return;
    try {
      await api.correctMemory(editing.id, { text: editing.text.trim() });
      setEditing(null);
      setProblem(null);
      list.reload();
      if (results) setResults((await api.searchMemory(query)).hits.map((hit) => hit.memory));
    } catch (caught: unknown) {
      setProblem(caught instanceof ApiError ? caught.message : "That did not work.");
    }
  };

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
      {problem && (
        <p className="panel__hint" role="alert">
          {problem}
        </p>
      )}
      {shown.length === 0 && !list.error && <p className="panel__hint">Nothing remembered yet.</p>}
      <ul className="items">
        {shown.map((memory) => (
          <li key={memory.id} className={`item${memory.forgotten ? " item--muted" : ""}`}>
            {editing?.id === memory.id ? (
              <form
                className="inspector__form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void saveCorrection();
                }}
              >
                <textarea
                  aria-label="Corrected memory"
                  className="inspector__input"
                  value={editing.text}
                  onChange={(event) => {
                    setEditing({ id: memory.id, text: event.target.value });
                  }}
                />
                <button
                  className="button button--small"
                  type="submit"
                  disabled={!editing.text.trim() || editing.text.trim() === memory.text}
                >
                  Save
                </button>
                <button
                  className="button button--small"
                  type="button"
                  onClick={() => {
                    setEditing(null);
                  }}
                >
                  Cancel
                </button>
              </form>
            ) : (
              <p className="item__text">{memory.text}</p>
            )}
            <p className="item__meta">
              {memory.kind} ·{" "}
              {memory.trust === "untrusted" ? "read on the web" : `trust: ${memory.trust}`}
              {memory.pinned && " · pinned"}
              {memory.forgotten && " · forgotten"}
              {memory.status === "superseded" && " · no longer true"}
              {memory.source?.startsWith("correction:") && " · your correction"}
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
                  {memory.status !== "superseded" && (
                    <button
                      className="button button--small"
                      type="button"
                      onClick={() => {
                        setEditing({ id: memory.id, text: memory.text });
                      }}
                    >
                      Correct
                    </button>
                  )}
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

/**
 * Leaving is easy, and erasing is real (docs/21 §7). Download gives the user everything. Erase is
 * two steps: a preview that changes nothing, then a confirmation of exactly what it showed.
 */
function DataTab({ api, save }: { api: InspectorApi; save: SaveFile }): JSX.Element {
  const [exporting, setExporting] = useState(false);
  const [phrase, setPhrase] = useState("");
  const [preview, setPreview] = useState<PurgePreview | null>(null);
  const [receipt, setReceipt] = useState<PurgeReceipt | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const attempt = async (work: () => Promise<void>) => {
    try {
      setProblem(null);
      await work();
    } catch (caught: unknown) {
      setProblem(caught instanceof ApiError ? caught.message : "That did not work.");
    }
  };
  const download = () =>
    attempt(async () => {
      setExporting(true);
      try {
        save(await api.exportAll(), "hedwig-export.zip");
      } finally {
        setExporting(false);
      }
    });
  const look = () =>
    attempt(async () => {
      setReceipt(null);
      setPreview(await api.purgePreview("phrase", phrase.trim()));
    });
  const erase = (confirm: string) =>
    attempt(async () => {
      setReceipt(await api.purge("phrase", phrase.trim(), confirm));
      setPreview(null);
      setPhrase("");
    });

  return (
    <>
      <h3 className="inspector__heading">Download everything</h3>
      <p className="panel__hint">
        Every conversation, memory and goal, as readable files and the full database.
      </p>
      <button
        className="button button--small"
        type="button"
        disabled={exporting}
        onClick={() => void download()}
      >
        {exporting ? "Preparing…" : "Download everything"}
      </button>

      <h3 className="inspector__heading">Erase for good</h3>
      <p className="panel__hint">
        Unlike Forget, this cannot be undone. It removes every message and memory containing the
        words, what HEDWIG made from them, and backups that still hold them.
      </p>
      <form
        className="inspector__form"
        onSubmit={(event) => {
          event.preventDefault();
          void look();
        }}
      >
        <input
          aria-label="Words to erase"
          className="inspector__input"
          placeholder="A name, a place, a phrase…"
          value={phrase}
          onChange={(event) => {
            setPhrase(event.target.value);
            setPreview(null);
          }}
        />
        <button className="button button--small" type="submit" disabled={phrase.trim().length < 3}>
          Preview
        </button>
      </form>
      {problem && (
        <p className="panel__hint" role="alert">
          {problem}
        </p>
      )}
      {preview && !preview.confirm && <p className="panel__hint">Nothing contains that.</p>}
      {preview?.confirm && (
        <section aria-label="Erase preview" className="item">
          <p className="item__text">This will permanently erase:</p>
          <ul>
            {Object.entries(preview.counts).map(([label, count]) => (
              <li key={label} className="item__meta">
                {count} {count === 1 ? label.replace(/s$/, "") : label}
              </li>
            ))}
          </ul>
          {preview.examples.map((example) => (
            <p key={example} className="item__meta">
              e.g. “{example}”
            </p>
          ))}
          {preview.backups > 0 && (
            <p className="item__meta">
              and delete {preview.backups} {preview.backups === 1 ? "backup" : "backups"}, which
              still contain it.
            </p>
          )}
          <div className="item__actions">
            <button
              className="button button--small button--danger"
              type="button"
              onClick={() => void erase(preview.confirm ?? "")}
            >
              Erase permanently
            </button>
            <button
              className="button button--small"
              type="button"
              onClick={() => {
                setPreview(null);
              }}
            >
              Cancel
            </button>
          </div>
        </section>
      )}
      {receipt && (
        <p className="panel__hint" role={receipt.verified === "failed" ? "alert" : "status"}>
          {receipt.verified === "failed"
            ? "Erased, but traces were found in the database file. Stop HEDWIG and run `hedwig purge --everything` to be sure."
            : `Erased${receipt.verified === "file" ? ", and checked against the database file" : ""}.` +
              (receipt.also_elsewhere > 0
                ? " Some of the same words are in things you did not choose to erase."
                : "")}
        </p>
      )}
    </>
  );
}
