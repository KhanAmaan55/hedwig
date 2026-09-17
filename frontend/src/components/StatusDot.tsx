import type { JSX } from "react";

import type { ConnectionState } from "../hooks/useHealth";

const LABELS: Record<ConnectionState, string> = {
  connecting: "Connecting to the backend",
  connected: "Backend connected",
  disconnected: "Backend unreachable",
};

export function StatusDot({ state }: { state: ConnectionState }): JSX.Element {
  return (
    <span
      className={`status-dot status-dot--${state}`}
      role="status"
      aria-label={LABELS[state]}
      title={LABELS[state]}
    />
  );
}
