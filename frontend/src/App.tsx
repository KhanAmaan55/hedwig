import { lazy, Suspense, type JSX } from "react";

import { useHealth } from "./hooks/useHealth";
import { StatusDot } from "./components/StatusDot";
import { PaneBoundary } from "./components/PaneBoundary";
import { SubsystemList } from "./components/SubsystemList";
import { formatDuration } from "./lib/format";
import ConversationPane from "./panes/conversation/ConversationPane";
import InspectorPane from "./panes/inspector/InspectorPane";

// Lazy: three.js and three-vrm are most of the bundle, and nothing else in the app needs them
// (docs/28 §6).
const AvatarPane = lazy(() => import("./panes/avatar/AvatarPane"));

/**
 * The conversation beside the avatar (docs/18 §2, §13.1).
 *
 * The Mind Inspector (UX-03) sits under the avatar; the health readout that was Milestone 1's
 * whole UI lives in a collapsed "System" section below it.
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

        <div className="layout">
          <PaneBoundary name="Conversation">
            <ConversationPane />
          </PaneBoundary>

          <aside className="layout__side">
            <section className="panel">
              <h2 className="panel__title">Avatar</h2>
              <Suspense fallback={<p className="panel__hint">Loading avatar…</p>}>
                <AvatarPane />
              </Suspense>
            </section>

            <PaneBoundary name="Mind Inspector">
              <InspectorPane />
            </PaneBoundary>

            {health !== null && (
              <details className="panel system">
                <summary className="panel__title system__summary">
                  System
                  <span className={`badge badge--${health.status}`}>{health.status}</span>
                </summary>
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
                <h3 className="system__heading">Subsystems</h3>
                <SubsystemList subsystems={health.subsystems} />
                {health.notices.length > 0 && (
                  <>
                    <h3 className="system__heading">Notices</h3>
                    <ul className="notices">
                      {health.notices.map((notice) => (
                        <li key={notice.code} className="notices__item">
                          <span className={`badge badge--${notice.level}`}>{notice.level}</span>
                          {notice.message}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </details>
            )}
          </aside>
        </div>
      </main>
    </div>
  );
}
