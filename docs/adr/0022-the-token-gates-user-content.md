# ADR-0022 — The API token gates user content, and is read from a file on every request

**Status:** Accepted
**Date:** 2026-11-20
**Affects:** docs/16 §3, §12, docs/21 §4, docs/18, `api/auth.py`, `cli.py`

## Context

docs/16 §3 requires a bearer token "on every request including WS", landing "with the first
endpoint that exposes anything personal". Milestone 7 then shipped `/v1/mind/emotion` and
`/v1/mind/expression` *without* it, and `routes/mind.py` explains why: the payload is HEDWIG's
own state — six floats and a rule name — and holds no user text. The two documents disagreed.

The conversation API (FND-02) is the first endpoint that returns what the user said, so the
token can no longer be deferred, and the disagreement has to be settled one way or the other.

Three facts constrain the answer:

* The avatar polls `/v1/mind/expression` several times a second, and `/v1/health` is how the
  shell and `scripts/dev.mjs` decide the backend is up — before any token could be delivered.
* The renderer runs in two places: inside Electron, and in a plain browser tab during
  development. A browser cannot read a file in `~/.hedwig`.
* `hedwig token rotate` must take effect without asking the user to restart the backend.

## Decision

1. **The token is required on every route that reads or writes user content** — conversation
   now; memory, goals, knowledge, export and the WebSocket as they land. It is a router-level
   dependency, so a new route under a guarded router cannot forget it.
2. **Health and HEDWIG's own mind state stay open.** `/v1/health` and `/v1/mind/emotion`,
   `/v1/mind/emotion/history`, `/v1/mind/expression` carry no user content. Personality and
   relationship routes, when they land, *are* guarded: the relationship is about the user.
3. **The token lives in `<data_dir>/token`**: 32 bytes from `secrets.token_urlsafe`, created at
   first start with mode `0600`, never logged, never in config, compared in constant time.
4. **It is re-read when the file changes.** The check stats the file and re-reads it only when
   its modification time differs from the cached one — a rotation is honoured on the next
   request, at the cost of one `stat` per guarded request.
5. **Clients obtain it from the file, not from the API.** The CLI reads it directly. The
   Electron main process reads it and the preload exposes it to the renderer. In browser
   development, the Vite dev server's proxy injects the header, so the page never holds it
   (UX-02).
6. **`Authorization: Bearer <token>` is the only accepted form on HTTP.** No cookie, no query
   string. The WebSocket form is decided when FND-05 lands (docs/16 §9 prefers
   `Sec-WebSocket-Protocol`).

## Consequences

* docs/16 §3 now says "every user-content route" rather than "every request"; the exception
  `routes/mind.py` described is the rule, written down once.
* Another process on the machine can still read HEDWIG's mood and see that it is up. It cannot
  read a single word the user wrote. That is the line the threat model in docs/21 §2 draws.
* A guarded request costs one `stat` call. Negligible next to a model call, and measurable if
  it ever matters.
* In browser development the Vite proxy is a token-bearing path on `localhost:5173`. Any page
  in the browser could post to it. This is accepted for development only, where the same
  person controls both ends; production uses Electron, which has no such proxy.

## Alternatives considered

* **Guard everything, health included.** Matches docs/16 §3's original sentence. Rejected:
  the shell's readiness probe and the avatar's poll would need the token before the renderer
  has one, and neither endpoint discloses anything a token would protect.
* **Hold the token in memory, generated per process.** No file to protect. Rejected: the CLI
  is a separate process and could never learn it.
* **Token in config or `.env`.** Rejected: config is printed by `hedwig config` and copied
  between machines; secrets never belong in a file the user edits (config/hedwig.toml header).
* **Cache the token for the life of the process.** One less `stat`. Rejected: rotation would
  silently not work until a restart, which is the kind of security control that looks
  present and is not.
* **OS keychain now.** The Phase-8 plan (docs/22). Rejected for now: a new dependency and three
  platform backends, for a threat the `0600` file already addresses on a single-user machine.

## Revisit if

* HEDWIG is ever bound beyond loopback (docs/16 §3 table) — then TLS is mandatory and the
  open routes should be reconsidered.
* A second user account on the same machine is a realistic deployment — then the keychain.
* The mind endpoints start carrying anything derived from user content (e.g. a "what HEDWIG is
  thinking about" field) — then they move behind the token.
