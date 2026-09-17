import type { JSX } from "react";

import { useHealth } from "./hooks/useHealth";
import { StatusDot } from "./components/StatusDot";
import { SubsystemList } from "./components/SubsystemList";
import { formatDuration } from "./lib/format";

/**
 * Milestone 1's entire UI: proof that the shell, the renderer and the backend are one
 * application.
 *
 * The three-pane layout from docs/18 §2 — conversation, avatar, Mind Inspector — arrives
 * with the subsystems it displays. Nothing here pretends to be those panes.
 */
export default function App(): JSX.Element {
  const { state, health, error, latencyMs, refresh } = useHealth();

  return (
    <div className="app">
      <header className="app__header">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true" />
          <div>
            <h1 className="brand__name">HEDWIG</h1>
            <p className="brand__tagline">
              Hybrid Evolving Digital Wisdom &amp; Intelligence Guardian
            </p>
          </div>
        </div>

        <div className="connection">
          <StatusDot state={state} />
          <span className="connection__label">
            {state === "connected" ? "backend connected" : state}
          </span>
          {latencyMs !== null && <span className="connection__latency">{latencyMs} ms</span>}
        </div>
      </header>

      <main className="app__main">
        {error !== null && (
          <section className="panel panel--error" role="alert">
            <h2 className="panel__title">Backend unreachable</h2>
            <p className="panel__body">{error}</p>
            <p className="panel__hint">
              Start it with <code>npm run dev</code>, or <code>uv run hedwig serve</code>.
            </p>
            <button className="button" onClick={refresh} type="button">
              Retry now
            </button>
          </section>
        )}

        {health !== null && (
          <>
            <section className="panel">
              <h2 className="panel__title">System</h2>
              <dl className="facts">
                <div className="facts__row">
                  <dt>version</dt>
                  <dd>{health.version}</dd>
                </div>
                <div className="facts__row">
                  <dt>environment</dt>
                  <dd>{health.environment}</dd>
                </div>
                <div className="facts__row">
                  <dt>uptime</dt>
                  <dd>{formatDuration(health.uptime_s)}</dd>
                </div>
                <div className="facts__row">
                  <dt>schema</dt>
                  <dd>{health.schema_version ?? "—"}</dd>
                </div>
              </dl>
            </section>

            <section className="panel">
              <h2 className="panel__title">Subsystems</h2>
              <SubsystemList subsystems={health.subsystems} />
            </section>

            {health.notices.length > 0 && (
              <section className="panel panel--warn">
                <h2 className="panel__title">Notices</h2>
                <ul className="notices">
                  {health.notices.map((notice) => (
                    <li key={notice.code} className="notices__item">
                      <span className={`badge badge--${notice.level}`}>{notice.level}</span>
                      {notice.message}
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}

        <section className="panel panel--muted">
          <h2 className="panel__title">Milestone 1 — Project Foundation</h2>
          <p className="panel__body">
            Structure, configuration, logging, ports and the desktop shell. No memory, no cognition,
            no model. The roadmap for what comes next is in <code>docs/22-roadmap.md</code>.
          </p>
        </section>
      </main>
    </div>
  );
}
