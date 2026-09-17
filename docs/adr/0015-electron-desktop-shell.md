# ADR-0015 — Electron desktop shell

**Status:** Accepted
**Date:** 2026-08-02
**Affects:** [docs/02](../02-system-architecture.md), [docs/17](../17-backend-architecture.md), [docs/18](../18-frontend-architecture.md), [docs/21](../21-security-privacy-ethics.md)

## Context

The original design served the built frontend as static files from the FastAPI backend and
listed a native desktop shell (Tauri) as a *future improvement*, triggered by "users want
an app icon rather than a browser tab" ([docs/17](../17-backend-architecture.md) §14).

Implementation of Milestone 1 brought that trigger forward: HEDWIG is a companion that runs
continuously and is opened dozens of times a day. A browser tab is the wrong container for
that — it is closed by accident, mixed in with unrelated tabs, and has no place in the dock
or the login items. The desktop shell also becomes the natural owner of the backend's
lifetime, which removes the "did you remember to start the server?" failure from the user's
experience entirely.

## Decision

Ship an Electron shell as the primary way to run HEDWIG.

* `electron/main.ts` owns the window and, when packaged, the backend process. It contains
  no application logic — it knows a URL and a health endpoint.
* `electron/preload.ts` exposes exactly one frozen object, `window.hedwig`, carrying the
  API base URL and the platform. No IPC channel carries application data; the renderer
  talks to the backend over the same HTTP API any client uses.
* The renderer runs with `contextIsolation: true`, `nodeIntegration: false`,
  `sandbox: true`, a strict CSP, and a navigation handler that sends every external link to
  the system browser.
* In development the shell attaches to the backend and Vite server started by
  `scripts/dev.mjs`; in production it spawns `hedwig serve` and loads the built renderer
  from disk.
* The backend keeps serving the built frontend, so a browser at `localhost:5173` (dev) or
  the served bundle (later) remains a fully supported client.

Electron over Tauri: the frontend, the future 3D avatar renderer (three.js/VRM) and the
developer are all in the JavaScript ecosystem already, and Tauri's advantage — bundle size —
matters little for an application that ships alongside multi-gigabyte model weights.
Electron's cost is memory, and one Chromium instance on a machine already running a local
LLM is not the binding constraint.

## Consequences

**Gained.** An application, not a tab. One process to start. The shell can later own OS
integration that the architecture already anticipates: tray presence, login items, native
notifications for the proactive items in [docs/11](../11-curiosity-engine.md) §7, and the
global shortcut that a companion wants.

**Cost.** A second runtime to keep updated and a real security surface — a renderer that
displays model output and, from Milestone 7, fetched web content. That surface is why the
security settings above are not defaults to be revisited but part of this decision. Also
~100 MB of binary at install time and roughly 150 MB of resident memory.

**Unchanged.** The API remains the only interface between the frontend and HEDWIG
([docs/16](../16-api-structure.md) §2). Nothing in the backend knows the shell exists. The
CLI remains a first-class client, and the browser remains a supported one — which is what
keeps this decision reversible.

## Alternatives considered

* **Backend-served static files only** (the original design) — rejected: no dock presence,
  no lifecycle ownership, and the user has to start a server by hand.
* **Tauri** — rejected for now: a second language in the build, a smaller ecosystem for the
  3D avatar work, and its bundle-size win is irrelevant here. Reconsider if memory becomes a
  real complaint; the shell is thin enough to port.
* **A packaged browser profile / PWA** — rejected: no control over lifecycle, no reliable
  loopback permissions, and the security posture is worse, not better.

## Revisit if

Memory footprint becomes a user-visible problem, or the shell starts accumulating logic
beyond window and lifecycle management. The second is the real risk: if `main.ts` ever needs
to know what a memory or a turn is, this decision has been violated rather than outgrown.
