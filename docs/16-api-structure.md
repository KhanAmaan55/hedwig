# 16 — API Structure

**Status:** Partially implemented (§12) · **Depends on:** [07](07-brain-langgraph-workflow.md), [15](15-avatar-controller.md) · **Depended on by:** [18](18-frontend-architecture.md)

---

## 1. Purpose

The API is the boundary between HEDWIG and any client. It exists so that the web UI is *a*
client rather than *the* client — a CLI, a terminal UI, a mobile app or a future avatar
renderer speak the same protocol.

Two surfaces, split by nature of the data:

- **REST** for state you fetch and modify: sessions, memory, mind state, goals, config.
- **WebSocket** for the live channel: token streams, expression frames, state updates,
  notices.

---

## 2. Principles

1. **Versioned from day one.** Everything under `/v1`. WebSocket envelopes carry `v`.
2. **The edge contains no domain logic** ([02](02-system-architecture.md) §3). It validates,
   authenticates, translates and streams.
3. **Introspection is a first-class feature, not an admin afterthought.** Memory, emotion,
   personality, goals, findings and traces are all queryable, because a companion you cannot
   inspect is one you cannot trust ([01](01-vision-and-scope.md) §6).
4. **Loopback by default.** `127.0.0.1`, no exceptions without explicit config.
5. **Errors are typed and machine-readable**, with a stable `code`.
6. **Every response that reflects a cognitive judgement carries provenance** — confidence,
   sources, or a trace id.

---

## 3. Authentication

> **Implemented** with the conversation API ([ADR-0022](adr/0022-the-token-gates-user-content.md),
> §12). The token guards every route that reads or writes **user content**. `/v1/health` and
> HEDWIG's own mind state (`/v1/mind/emotion`, `/v1/mind/expression`) stay open: they carry no
> user text, and the shell's readiness probe and the avatar's poll need them before a token
> can be delivered.

| Deployment | Mechanism |
|---|---|
| Local (default) | Bearer token generated at first run, stored in `<data_dir>/token` (`0600`), required on every user-content route including WS. Loopback-bound. Rotated with `hedwig token rotate`, honoured on the next request. |
| Exposed (user opts in) | Same token plus a mandatory TLS terminator in front; a startup warning if bound to a non-loopback address without TLS |

No accounts, no OAuth, no sessions-as-cookies. A single-user local application that grows a
login screen has gained an attack surface and a support burden and lost nothing it needed.
The token exists to stop other processes and other browser tabs on the machine from talking to
HEDWIG, which is a real threat on a shared desktop.

---

## 4. REST resources

Base: `http://127.0.0.1:8730/v1`

### 4.1 Conversation

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/sessions` | Open a session → `{session_id}` |
| `GET` | `/sessions?limit=&before=` | List sessions with summaries |
| `GET` | `/sessions/{id}` | Session detail: turns, mood arc, summary |
| `POST` | `/sessions/{id}/end` | Close a session (triggers T1 reflection) |
| `POST` | `/sessions/{id}/messages` | Send a message. Non-streaming convenience path; returns the complete reply. WS is the primary path. |
| `GET` | `/sessions/{id}/messages?limit=&before=` | Paginated history |
| `POST` | `/messages/{id}/feedback` | `{signal: 1|-1, note?}` → personality evidence + memory reinforcement |

### 4.2 Memory

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/memory/search?q=&kinds=&limit=&min_confidence=` | Search as HEDWIG would, returning scores and score breakdowns |
| `GET` | `/memory/{id}` | One memory with full provenance and derivation lineage |
| `GET` | `/memory/{id}/lineage` | The derivation graph up and down |
| `POST` | `/memory/{id}/pin` · `DELETE` | Protect from forgetting |
| `POST` | `/memory/{id}/forget` | Tombstone (reversible) |
| `POST` | `/memory/{id}/restore` | Undo a tombstone |
| `PATCH` | `/memory/{id}` | Correct content or confidence — user corrections are `USER` trust and supersede (built: MEM-03, returns the replacement; docs/06 §18) |
| `GET` | `/memory/timeline?from=&to=` | Episodes over a period, for the timeline view |
| `GET` | `/entities?q=` · `GET /entities/{id}` | Entities, their memories, relationship state |
| `GET` | `/beliefs?subject=&status=` | Beliefs, including superseded history |

`PATCH /memory/{id}` matters more than it looks: a memory system without user correction
accumulates errors forever, and the user is the highest-trust source available.

### 4.3 Mind state

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/mind` | Full `MindSnapshot` |
| `GET` | `/mind/emotion` · `/mind/emotion/history?from=&to=&resolution=` | Current + timeline |
| `GET` | `/mind/expression` | The emotional state resolved into semantic avatar intent ([28](28-avatar-mvp.md) §4) |
| `GET` | `/mind/personality` | Traits with anchors, drift, bounds |
| `GET` | `/mind/personality/history` | Every applied change with evidence |
| `POST` | `/mind/personality/revert/{history_id}` | Undo one drift |
| `POST` | `/mind/personality/reset?trait=` | Reset to anchor |
| `GET` | `/mind/personality/proposals?status=` | Pending and rejected proposals with the rule that fired |
| `GET` | `/mind/identity` · `PATCH` | Identity core; PATCH requires `confirm=true` and is audited |
| `GET`/`POST`/`PATCH` | `/goals` | Goal CRUD |
| `GET` | `/relationships` · `/relationships/{entity_id}` | Relationship state and trajectory |
| `GET` | `/mind/snapshots` · `POST /mind/snapshots/{date}/restore` | Identity snapshot rollback |

### 4.4 Curiosity, knowledge, reflection

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/curiosity/gaps?status=` | The gap register — useful even with curiosity disabled |
| `POST` | `/curiosity/gaps` | User-registered question |
| `GET` | `/curiosity/findings?status=` | Findings feed (the passive delivery channel) |
| `POST` | `/curiosity/findings/{id}/dismiss` · `/confirm` | Dismiss (raises the topic threshold) or confirm (promotes to `active`) |
| `GET`/`POST`/`DELETE` | `/sources` | Allowlist management |
| `POST` | `/curiosity/explore` | Manual trigger, still budget-bound |
| `GET`/`POST`/`DELETE` | `/documents` | Ingest, list, remove local documents |
| `POST` | `/documents/reindex` | Re-ingest |
| `GET` | `/reflection/runs?tier=` | Run history and stats (built as `GET /reflection/runs?limit=`, T2 only — REF-02) |
| `POST` | `/reflection/trigger?tier=` | Manual trigger (dev and impatience) (built as `POST /reflection/run` — REF-02) |
| `GET` | `/reflection/disputes` | Beliefs the nightly run thinks repeat or conflict, waiting for the user (REF-02) |
| `POST` | `/reflection/disputes/{pair}/resolve` | `{"choice": "first" \| "second" \| "both" \| "same"}` — the user settles one (REF-02) |
| `GET` | `/reflection/report/latest` | The T4 identity report |

### 4.5 System and introspection

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness plus per-subsystem status (models, db, indexes, jobs) |
| `GET` | `/explain/{message_id}` | **Why HEDWIG said that** — see §6 |
| `GET` | `/traces/{correlation_id}` | The causal event graph for a turn or job |
| `GET` | `/metrics` | Prometheus text format |
| `GET`/`PATCH` | `/config` | Safe config subset; secrets never returned |
| `GET` | `/jobs` · `POST /jobs/{name}/trigger` · `/pause` | Job control |
| `GET` | `/notices` | Unresolved system notices (dead letters, degraded subsystems, pending approvals) |
| `POST` | `/export` | Full portable export, streamed as a zip (built: synchronous, not a job — SEC-02) |
| `POST` | `/purge/preview` | `{scope, value}` → counts, examples, backups affected, `confirm` code; changes nothing (SEC-02) |
| `POST` | `/purge` | Privacy purge; `{scope, value, confirm}` ([21](21-security-privacy-ethics.md) §7.4) |
| `GET` | `/purge/receipts` | Past purges: scope kind, counts, verification; never content (SEC-02) |

### 4.6 Error format

```jsonc
{
  "error": {
    "code": "capability_unavailable",   // stable, enumerated
    "message": "The conversational model is not available.",
    "detail": { "tier": "conversational", "model": "llama3.1:8b" },
    "correlation_id": "turn_01JQ8Z...",
    "retryable": true
  }
}
```

Codes: `invalid_request`, `not_found`, `conflict`, `capability_unavailable`,
`budget_exhausted`, `approval_required`, `degraded`, `internal`. HTTP status is set
accordingly, but clients branch on `code`.

---

## 5. WebSocket protocol

`ws://127.0.0.1:8730/v1/stream?token=…` — one connection carries everything.

### 5.1 Envelope

```jsonc
{ "v": 1, "id": "01JQ…", "type": "…", "ts": 1753900923.114,
  "corr": "turn_01JQ…", "payload": { } }
```

### 5.2 Client → server

| Type | Payload | Notes |
|---|---|---|
| `hello` | `{client, capabilities: ["avatar","inspector"], subscriptions: [...]}` | Determines what the server sends; frames are not emitted unless `avatar` is present |
| `message.send` | `{session_id, text, client_message_id}` | `client_message_id` makes retries idempotent |
| `interrupt` | `{turn_id}` | Cancel a turn in flight |
| `approval.respond` | `{call_id, decision, edited_args?}` | Answers a tool approval interrupt |
| `feedback` | `{message_id, signal, note?}` | Same as REST, on the live channel |
| `presence` | `{focused, typing}` | Feeds the interruption policy and idle detection |
| `ping` | `{}` | Keepalive |

### 5.3 Server → client

| Type | Payload | Lossy? |
|---|---|---|
| `turn.started` | `{turn_id, session_id}` | no |
| `turn.stage` | `{turn_id, stage, detail?}` | yes — drives "recalling…", "thinking…" progress |
| `token` | `{turn_id, text}` | **no** — reassembled into the reply |
| `message.complete` | `{message_id, text, cited_memory_ids, trace_id}` | no |
| `turn.completed` | `{turn_id, status, metrics}` | no |
| `expression.frame` | see [15](15-avatar-controller.md) §4 | yes |
| `mind.update` | `{emotion?, personality?, goals?}` | yes (snapshots) |
| `memory.event` | `{kind, memory_id, summary}` | yes |
| `notice` | `{level, code, message, actions?}` | no |
| `approval.request` | `{call_id, tool, args, reason}` | no |
| `error` | error object | no |
| `pong` | `{}` | yes |

The lossy column is the same discipline as [04](04-communication-and-event-bus.md) §7: under
backpressure, expression frames and mind updates are dropped newest-wins, while tokens and
completions are never dropped. Getting this wrong produces garbled replies under load, which
looks like a model bug and is not.

### 5.4 Turn lifecycle over WS

```mermaid
sequenceDiagram
    participant C as Client
    participant S as Server
    C->>S: hello {capabilities:[avatar,inspector]}
    S->>C: mind.update (initial snapshot)
    C->>S: message.send {text, client_message_id}
    S->>C: turn.started
    S->>C: turn.stage {recalling}
    S->>C: turn.stage {deliberating}
    opt tool needs approval
        S->>C: approval.request
        C->>S: approval.respond {approve}
    end
    S->>C: turn.stage {composing}
    loop streaming
        S->>C: token
        S->>C: expression.frame (10 Hz, interleaved)
    end
    S->>C: message.complete {cited_memory_ids, trace_id}
    S->>C: turn.completed {metrics}
    S->>C: mind.update (post-appraisal, ~30 s later)
    S->>C: memory.event (capture results)
```

### 5.5 Reconnection

Reconnection carries `last_event_id`. The server replays non-lossy messages for in-flight and
recently-completed turns from the turn record. A turn keeps running server-side while the
client is away and is fully persisted — **closing the browser must never lose a reply**, which
is a property that falls out of the turn being checkpointed and persisted rather than being a
property of the connection.

---

## 6. The explain endpoint

`GET /v1/explain/{message_id}` is the feature that makes an emotional, memory-driven agent
debuggable and trustworthy. It is specified here because it constrains what other subsystems
must record.

```jsonc
{
  "message_id": "01JQ…",
  "reply_excerpt": "You mentioned Lisbon last month, so…",
  "mind_at_turn": { "emotion": {...}, "personality": {...}, "goals": [...] },
  "directives": ["Be concise but complete.", "Give a clear recommendation."],
  "retrieval": {
    "queries": ["lisbon move", "user location"],
    "policy": { "...": "..." },
    "items": [
      { "memory_id": "01JQ…", "kind": "belief", "text": "The user lives in Lisbon",
        "score": 0.82,
        "breakdown": { "lexical_rank": 3, "semantic_rank": 1, "salience": 0.71,
                       "confidence": 0.9, "recency": 1.12, "trust": 1.0 },
        "provenance": { "tier": "user", "source_ref": "session_01JQ…" },
        "cited_in_reply": true }
    ],
    "dropped_count": 14
  },
  "tools": [ { "name": "get_datetime", "status": "ok" } ],
  "model": { "tier": "conversational", "model_id": "llama3.1:8b",
             "prompt_version": "compose@7", "tokens_in": 2841, "tokens_out": 190 },
  "trace_id": "turn_01JQ…"
}
```

Everything here is already recorded for other reasons (`working_set_log`, `llm_call`,
`tool_call`, `emotion_history`, `personality_history`) — the endpoint is an assembly, not new
instrumentation. That is deliberate: designing the explain endpoint early is what forced those
tables to carry the right columns.

---

## 7. Streaming, backpressure and limits

| Concern | Approach |
|---|---|
| Backpressure | Per-connection bounded queue (512). Lossy types drop newest-wins; non-lossy types apply backpressure to the producer, and a persistently slow client is disconnected with a notice |
| Rate limits | 60 messages/min, 10 concurrent turns/session (in practice 1) |
| Payload caps | Message text 32 KB; REST body 1 MB |
| Timeouts | Idle WS 5 min without a ping → close; HTTP 30 s except explicitly long endpoints |
| Compression | `permessage-deflate` enabled; expression frames benefit most |
| CORS | Locked to the configured frontend origin; `*` never |
| Pagination | Cursor-based (`before=<ulid>`), never offset — ULIDs make this natural |

---

## 8. Tradeoffs

| Decision | Gained | Given up | Revisit if |
|---|---|---|---|
| REST + one WebSocket | Clear split; one live connection to manage | Two client code paths | Never |
| One WS for chat, avatar and telemetry | One connection, one auth, shared backpressure policy | Frame traffic shares a socket with tokens | Avatar traffic starts affecting token latency → then a second socket |
| Bearer token only | Simple, adequate for local | Not suitable for exposure without TLS | Multi-user (a non-goal) |
| Introspection endpoints as first-class | Debuggability and trust | Surface area to maintain and secure | Never |
| Typed error codes | Clients can branch reliably | An enum to maintain | Never |
| `/explain` as an assembly of existing records | No extra instrumentation cost | Constrains other subsystems to record enough | Never — this constraint is a feature |
| Cursor pagination on ULIDs | Correct under concurrent writes | No random page access | Never |
| Turn survives client disconnect | Never lose a reply | Server does work nobody may read | Never |

---

## 9. Failure modes

| Failure | Response |
|---|---|
| Model unavailable mid-stream | `error` frame with `capability_unavailable`, partial reply preserved and persisted, turn marked `truncated` |
| Client disconnects mid-turn | Turn completes and persists; replayed on reconnect |
| Slow client | Lossy drops first, then disconnect with a notice |
| Duplicate `message.send` (retry) | `client_message_id` dedupe returns the existing turn |
| Approval never answered | Interrupt expires after 10 min; turn composes without the tool |
| Token in a URL query (WS) | Accepted because browsers cannot set WS headers, but logged with the token redacted; a `Sec-WebSocket-Protocol` token path is offered as the preferred alternative |
| Two clients connected | Both receive everything; both may send. Last-write-wins on `presence`. This is a feature (desktop + phone), not a bug |
| Version mismatch | Server rejects unknown `v` with `invalid_request` and the supported range |

---

## 10. Testing

- **Contract tests** — every endpoint against a golden OpenAPI schema (generated by FastAPI,
  checked in, drift fails CI).
- **WS protocol tests** — full turn lifecycle, interrupt, approval, reconnect-with-replay,
  duplicate send, slow-client backpressure.
- **Lossy/non-lossy test** — flood a connection; assert tokens are never dropped and frames
  are.
- **Auth tests** — every route rejects a missing/invalid token; non-loopback bind without TLS
  warns.
- **Explain completeness test** — for a turn using memory and a tool, assert every field is
  populated and every cited memory id resolves.
- **Pagination test** — concurrent writes during pagination never skip or duplicate.
- **CLI-parity test** — the CLI uses only the public API, so the API is proven complete by the
  CLI's existence.

---

## 11. Future improvements

| Improvement | Trigger |
|---|---|
| SSE fallback for token streaming | A client environment cannot use WebSockets |
| Webhook/outbound notifications (desktop notifications for proactive items) | Phase 7 proactivity |
| Read-only "guest" token scope | Sharing the inspector without granting chat |
| Batch endpoints for memory operations | The inspector needs bulk edits |
| gRPC or msgpack for the frame channel | Frame bandwidth becomes measurable |
| API for external avatar renderers (Unreal bridge) | Someone builds one; the protocol already supports it |

---

## 12. Implementation record — the conversation surface (FND-01, FND-02, UX-01)

The first endpoints that carry what the user said, and the first way for a person to talk to
HEDWIG outside the test suite. Backlog: [29](29-backlog.md).

### 12.1 What exists

| Method | Path | Guarded | Notes |
|---|---|---|---|
| `POST` | `/v1/sessions` | yes | `{channel?: "cli"\|"web"\|"api"}` → `201 {session_id, channel, started_at}` |
| `GET` | `/v1/sessions?limit=` | yes | Most recent first, with `turn_count` and `ended_at` |
| `GET` | `/v1/sessions/{id}` | yes | One session, or 404 |
| `POST` | `/v1/sessions/{id}/messages` | yes | `{text}` (1–32 768 chars) → the complete reply. 404 for an unknown session |
| `GET` | `/v1/sessions/{id}/messages?limit=&before=` | yes | Oldest first; `before` is a message id; `next_before` is the cursor for the older page |
| `POST` | `/v1/sessions/{id}/end` | yes | Ends it now (`end_reason: "explicit"`); waits for an in-flight turn; idempotent; 404 if unknown (FND-03, docs/27 §5.1) |

Concurrent messages to one session are answered one after another, never interleaved (FND-04,
docs/07 §16). Feedback and the WebSocket remain backlog stories (FND-05).

### 12.2 One request, end to end

```mermaid
sequenceDiagram
    participant C as Client (CLI / UI)
    participant A as api/routes/conversation
    participant T as api/auth (TokenFile)
    participant H as ConversationHistory
    participant B as Brain
    C->>A: POST /v1/sessions {channel: "cli"}
    A->>T: require_token (stat token file, compare_digest)
    A->>B: open_session(channel)
    B-->>A: session_id  (announces conversation.session.started)
    A-->>C: 201 {session_id}
    C->>A: POST /v1/sessions/{id}/messages {text}
    A->>T: require_token
    A->>H: get_session(id)  → 404 if unknown
    A->>B: run_turn(text, session_id)
    Note over B: ingest → guard → mind → recall → deliberate → compose → finalize
    B-->>A: final TurnState
    alt status == failed (no model)
        A-->>C: 503 capability_unavailable {turn_id, reply}
    else completed / refused / truncated
        A-->>C: 200 {turn_id, message_id, reply, status, intent, cited_memory_ids, mood}
    end
```

### 12.3 Decisions inside the story

* **The brain opens sessions, not the route.** `Brain.open_session(channel)` mints the id,
  opens the row, and announces `conversation.session.started`. Had the route called the
  session store directly, the turn's `ingest` would find the session already open and never
  announce it — and emotion appraises that event. One owner for the announcement.
* **Reading history is a separate port.** `ConversationHistory` (`get_session`,
  `list_sessions`, `history`) sits beside `Conversation` in `core/ports/sessions.py`. The
  brain's port stays five methods wide (docs/03 §5): a node has no business paginating. The
  same `SqliteSessionStore` satisfies both, and the container exposes it twice, typed by role.
* **A failed turn is a 503 that still carries the reply.** `compose` already turns a model
  outage into an honest reply and a `failed` status (docs/07 §8). The route reports it as
  `capability_unavailable` with `turn_id` and `reply` in `detail`, so a client can branch on
  the code and still show what HEDWIG said.
* **Non-streaming first.** The REST path returns the whole reply. The WebSocket is the primary
  path in §5 and the next transport story; building REST first gives the CLI a stable surface
  and the WS a reference to be tested against.
* **The CLI is a client, not a container.** `hedwig chat` uses `hedwig.client.HedwigClient`,
  which speaks HTTP and imports nothing else from HEDWIG — an import-linter contract enforces
  it. That is the CLI-parity rule (§10, docs/17 §9) made structural rather than aspirational.

### 12.4 Tradeoffs

| Decision | Gained | Given up | Revisit if |
|---|---|---|---|
| REST before WS | A small, testable surface the CLI can use today | No token streaming; a slow model shows nothing until it finishes | FND-05 |
| `before=<message id>` resolved to `seq` | Cursor stable under concurrent writes; ids stay opaque | One extra lookup per page | Never |
| Unknown session → 404 rather than auto-create | Typos and stale ids fail loudly | Client must `POST /sessions` first | Never |
| No `client_message_id` dedupe yet | Story stays small | A retried POST runs a second turn | FND-06 |

### 12.5 Future improvements

* T1 session reflection on `session.ended`, now that ends are prompt (REF-01).
* `/explain/{message_id}` from the records this path already writes (SEC-01).
* Rate limit (§7: 60 messages/min) once a client exists that could exceed it.

---

## 13. Implementation record — the stream (FND-05)

`ws://127.0.0.1:8730/v1/stream`, the envelope of §5.1. What is built:

| Direction | Type | Payload |
|---|---|---|
| → server | `hello` | `{client}` → answered with `ready {server_version}` |
| → server | `message.send` | `{session_id?, text, client_message_id?}`; no `session_id` opens one |
| → server | `ping` | `{}` → `pong` |
| ← client | `turn.started` | `{turn_id, session_id, client_message_id}` |
| ← client | `turn.stage` | `{stage: recalling · deliberating · acting · composing, detail?}` |
| ← client | `token` | `{text}`, in order, never dropped |
| ← client | `message.complete` | the same body `POST …/messages` returns (§12) |
| ← client | `turn.completed` | `{turn_id, status}` |
| ← client | `error` | the §4.6 error object, plus `client_message_id` when it answers a send |

* **Authentication.** Browsers cannot set headers on a WebSocket. The client offers two
  subprotocols, `hedwig.v1` and `hedwig.token.<token>`; the server checks the second and answers
  with the first. An `Authorization` header is accepted too, which is how the Vite proxy
  authenticates in browser development. Never a query string (§9). A bad token closes the socket
  with code 4401 before anything else is read.
* **How tokens get out.** Through the context-scoped live sink of
  [ADR-0023](adr/0023-live-turn-output-through-a-scoped-sink.md): no bus, no persistence, no
  port change. `message.complete` is authoritative; streamed text is a preview of it.
* **Ordering and backpressure.** One send lock per connection, so envelopes never interleave.
  Tokens are sent in order and awaited, so a slow client slows its own turn's delivery and never
  another's. Turns on one session still run one at a time (docs/07 §16).
* **Disconnects.** A turn keeps running when its client goes, and completes and persists.
  Reconnecting and loading history shows the reply. Replaying the live stream on reconnect (§5.5)
  is not built.
* **Not built yet:** `interrupt` (cancelling a turn mid-graph needs the turn to be resumable or
  cleanly failed, COG-03), `approval.*` (COG-03), `client_message_id` dedupe across reconnects
  (FND-06), `mind.update`/`expression.frame` (the avatar still polls; EMO-02), `presence`.

**The app.** `frontend/src/transport/socket.ts` connects on the first send, offers the token as a
subprotocol, asks for a fresh token once after a 4401 (a rotation), and keeps the connection
alive with a ping every 25 s. The conversation pane shows the stage ("remembering…", "putting
it into words…"), then the words as they arrive, and replaces them with `message.complete`'s
reply. If the socket cannot open, the same message goes over REST. If it drops after the turn
began, the pane says the reply is saved. The mouth moves with arriving words; with voice on,
the reply is spoken once it is complete.

**Verified live** (browser pane, `gemma4:e2b`, through the Vite proxy): the stage label 52 ms after
sending; the first token at 7.9 s on a cold first call; 3 to 249 characters over ~550 ms; one
reply on screen afterwards, identical to the persisted message. Streaming does not shorten a
cold model's time to first token (G12). It makes the wait legible.

---

## 14. Implementation record — why HEDWIG said that (SEC-01)

`GET /v1/explain/{message_id}`, behind the token, for any reply HEDWIG gave. It is an
**assembly**, as §6 intended: `hedwig.explain` reads the records the turn already left, under
docs/08 §3.1's rule (reads go to the tables, writes go through the owner). It writes nothing.

```mermaid
graph LR
    M[("message<br/><small>reply · meta · emotion_ref</small>")] --> X["explain.Explainer"]
    T[("turn<br/><small>status · latency · working_set_id · correlation</small>")] --> X
    W[("working_set_log<br/><small>queries · items · scores</small>")] --> X
    MEM["memory.inspect<br/><small>what each item says, even if forgotten since</small>"] --> X
    L[("llm_call")] -->|by correlation| X
    TC[("tool_call")] -->|by correlation| X
    E[("emotion_history")] -->|emotion_ref| X
    X --> R["/v1/explain/{id}"]
```

| Field | From |
|---|---|
| `question`, `reply` | the turn's user and reply messages |
| `intent`, `rationale`, `directives` | the reply's `meta`, **new**: the planner's rationale and every directive the reply was composed under (personality, relationship, emotion, plan) |
| `mood` | the `emotion_history` row the reply points at |
| `retrieval` | `working_set_log`: queries, each memory's score and trust, what it says now, and whether it has been forgotten since |
| `tools` | `tool_call` rows with the turn's correlation id |
| `model` | `llm_call` rows with the turn's correlation id: model, tier, tokens, timings |
| `trace_id` | the turn's correlation id |

* **The one record that was missing.** Directives and the planner's rationale were computed
  every turn and never stored, so "why so short?" had no answer afterwards. §6 predicted this
  ("designing the explain endpoint early is what forced those tables to carry the right
  columns"). They now go into the reply message's `meta`, a JSON column that already existed: no
  migration. Replies from before this change explain everything else and say directives were not
  recorded.
* **Honest about absence.** Every section is present, and empty or `null` when there was nothing
  (no recall run, no tool, no mood engine), never omitted, so a client can tell "nothing
  happened" from "not recorded".
* **In the app**, each HEDWIG reply has a *Why?* toggle showing the same assembly.

**Not built:** `prompt_version` per call (it is in the request's metadata, which `llm_call` does
not store; recording it is a column, worth adding when the first regression needs bisecting,
docs/19 §12). Also the causal event graph of `/traces/{correlation_id}`.

**Verified live** on a streamed `gemma4:e2b` turn ("What time is it right now?"): *Why?* showed
the search that found nothing, `get_datetime — ok`, the seven directives, the plan, the mood
at the time and `gemma4:e2b, 404 tokens in, 22 out, 4.5 s`. The model and tool records joined
through the streamed turn's correlation id.

