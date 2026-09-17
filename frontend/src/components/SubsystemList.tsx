import type { JSX } from "react";

import type { Subsystem } from "../transport/types";

export function SubsystemList({
  subsystems,
}: {
  subsystems: Record<string, Subsystem>;
}): JSX.Element {
  const entries = Object.entries(subsystems);

  if (entries.length === 0) {
    return <p className="panel__body">No subsystems are running yet.</p>;
  }

  return (
    <ul className="subsystems">
      {entries.map(([name, subsystem]) => (
        <li key={name} className="subsystems__item">
          <span className={`badge badge--${subsystem.status}`}>{subsystem.status}</span>
          <span className="subsystems__name">{name}</span>
          {Object.keys(subsystem.detail).length > 0 && (
            <span className="subsystems__detail">
              {Object.entries(subsystem.detail)
                .map(([key, value]) => `${key}=${String(value)}`)
                .join(" ")}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
