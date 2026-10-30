/**
 * The avatar pane (docs/18 §6, docs/28 §5).
 *
 * Decides *whether* and *what* to render — WebGL available, which model, reduced motion — and
 * hands the renderer a source and an expression. Everything about how the face moves lives below
 * it, in code that never sees React state.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type JSX,
} from "react";

import type { ExpressionIntent } from "../../transport/types";

import { DEFAULT_MODEL, hasWebGL } from "./environment";
import { useExpression } from "./useExpression";
import { VrmAvatar, type AvatarStatus } from "./VrmAvatar";

function usePrefersReducedMotion(): boolean {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(
    () => typeof window.matchMedia === "function" && window.matchMedia(query).matches,
  );
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(query);
    const change = () => {
      setReduced(media.matches);
    };
    media.addEventListener("change", change);
    return () => {
      media.removeEventListener("change", change);
    };
  }, []);
  return reduced;
}

export interface AvatarPaneProps {
  /** Injected in tests; detected otherwise. */
  readonly webgl?: boolean;
}

export default function AvatarPane({ webgl }: AvatarPaneProps): JSX.Element {
  const [supported] = useState(() => webgl ?? hasWebGL());
  const [dropped, setDropped] = useState<string | null>(null);
  const [status, setStatus] = useState<AvatarStatus>({ state: "loading" });
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const reducedMotion = usePrefersReducedMotion();
  const { expression, connected } = useExpression();

  // An object URL holds the whole file in memory until revoked.
  useEffect(() => {
    return () => {
      if (dropped) URL.revokeObjectURL(dropped);
    };
  }, [dropped]);

  const loadFile = useCallback((file: File | undefined) => {
    if (!file) return;
    setDropped(URL.createObjectURL(file));
  }, []);

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    setDragging(false);
    loadFile(event.dataTransfer.files[0]);
  };
  const onPick = (event: ChangeEvent<HTMLInputElement>) => {
    loadFile(event.target.files?.[0]);
  };

  if (!supported) {
    return (
      <div className="avatar avatar--empty" role="status">
        <p>This display can’t run WebGL, so the 3D avatar is unavailable.</p>
        <p className="avatar__hint">A 2D avatar for this case is planned (docs/28 §9).</p>
      </div>
    );
  }

  return (
    <div
      className={`avatar${dragging ? " avatar--dragging" : ""}`}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => {
        setDragging(false);
      }}
      onDrop={onDrop}
    >
      <VrmAvatar
        src={dropped ?? DEFAULT_MODEL}
        expression={expression}
        reducedMotion={reducedMotion}
        onStatus={setStatus}
      />

      {status.state !== "ready" && (
        <div className="avatar__overlay" role="status">
          {status.state === "loading" && <p>Loading avatar…</p>}
          {status.state === "missing" && (
            <>
              <p>No avatar model yet.</p>
              <p className="avatar__hint">
                Drop a <code>.vrm</code> file here, or place one at{" "}
                <code>frontend/public/{DEFAULT_MODEL}</code>.
              </p>
            </>
          )}
          {status.state === "error" && (
            <>
              <p>Couldn’t load that model.</p>
              <p className="avatar__hint">{status.message}</p>
            </>
          )}
        </div>
      )}

      <footer className="avatar__footer">
        {status.state === "ready" ? (
          <span className="avatar__model" title={status.model.license}>
            {status.model.name} · {status.model.authors} ·{" "}
            {status.model.licenseUrl ? (
              <a href={status.model.licenseUrl} target="_blank" rel="noreferrer">
                {status.model.license}
              </a>
            ) : (
              status.model.license
            )}
          </span>
        ) : (
          <span />
        )}
        <span className="avatar__actions">
          {!connected && <span className="badge badge--warn">mood offline</span>}
          <button
            className="button button--small"
            type="button"
            onClick={() => {
              picker.current?.click();
            }}
          >
            Load a .vrm…
          </button>
          <input ref={picker} type="file" accept=".vrm" hidden onChange={onPick} />
        </span>
      </footer>

      {expression && <ExpressionReadout expression={expression} />}
    </div>
  );
}

/** What the face is being asked to express. Visible so "why does it look like that?" can be
 * answered by looking, not by reading the network tab. */
function ExpressionReadout({ expression }: { expression: ExpressionIntent }): JSX.Element {
  const overlays = Object.entries(expression.overlay)
    .filter(([, weight]) => weight >= 0.05)
    .sort(([, a], [, b]) => b - a);
  return (
    <dl className="avatar__readout" aria-label="Current expression">
      <div>
        <dt>mood</dt>
        <dd>
          valence {expression.mood.valence.toFixed(2)} · arousal{" "}
          {expression.mood.arousal.toFixed(2)}
        </dd>
      </div>
      <div>
        <dt>showing</dt>
        <dd>
          {overlays.map(([name, weight]) => `${name} ${weight.toFixed(2)}`).join(" · ") ||
            "neutral"}
        </dd>
      </div>
    </dl>
  );
}
