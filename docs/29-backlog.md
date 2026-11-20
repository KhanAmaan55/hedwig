# 29 — Capability Assessment and Story Backlog

**Status:** Living document · **Last assessed:** 2026-11-20 · **Depends on:** [01](01-vision-and-scope.md), [22](22-roadmap.md)

---

## 1. Purpose

[22-roadmap.md](22-roadmap.md) says *what order* HEDWIG is built in, phase by phase. This
document says *where it stands* against the product vision, and holds the story backlog that
work is actually picked from. It is the resumption point for any development session: read
§3 for the current state, §5 for the next story, and update both when a story lands.

The implementation-status table in [docs/README.md](README.md#implementation-status) remains
the map of *which modules exist*. This document judges *whether the product does what the
vision asks*, which is a different question — a module can exist, pass its tests, and still not
be reachable by the person it was built for.

### Status vocabulary

| Status | Meaning |
|---|---|
| **COMPLETE** | Implemented, wired into the running system, and tested |
| **PARTIAL** | Exists, but missing functionality the vision asks for |
| **MISSING** | Not implemented |
| **BROKEN** | Exists but does not work correctly in the running system |
| **NEEDS_REVIEW** | Works, but has an architectural or quality concern worth resolving |

Nothing is marked COMPLETE because a class exists. Each judgement below cites the code path
or test that supports it.

---

## 2. Method (2026-11-20)

Read: `README.md`, `CLAUDE.md`, docs 01–28, ADRs 0001–0021, `src/hedwig/**`, `tests/**`,
`frontend/src/**`, `config/`, `migrations/`. Ran the Python suite: **883 passed** offline in
the recorded-provider harness. Traced the composition root (`wiring.py`) to see what is
*registered and scheduled*, not merely constructed.

---

## 3. Capability assessment

### 3.1 The headline finding

**Every cognitive subsystem built so far is reachable only from tests.** The brain runs real
turns against real memory, emotion and personality, and the LLM gateway talks to Ollama — but
the API exposes `/v1/health` and `/v1/mind/*` only, the CLI has no `chat`, and the desktop UI
shows health and the avatar. A person who opens HEDWIG today cannot say anything to it.

That makes the conversation surface (FND-01, FND-02, UX-01, UX-02) the highest-value work in
the repository, ahead of any new cognitive capability.

### 3.2 By capability

| Vision § | Capability | Status | Evidence |
|---|---|---|---|
| 15 | Open HEDWIG and talk to it | **COMPLETE** (was MISSING) | Desktop/browser conversation pane, `hedwig chat` and the REST API, verified live against Ollama (§6). Non-streaming: a slow model shows only "thinking…" until it finishes (FND-05) |
| 2 | Brain: turn graph, routing, caps, deterministic planner | **COMPLETE** | `brain/graph.py`, `planner.py`; `test_brain_graph.py`, `test_brain_routing.py` |
| 2 | Brain: safety guard | **PARTIAL** | `PermissiveGuard` stub — honest in `/health` (`brain` degraded) |
| 2 | Brain: decides external knowledge / tools are needed | **MISSING** | `StubToolRunner`; planner names only `get_datetime`/`calculate` |
| 2 | Brain: clarification | **PARTIAL** | Rule-based `CLARIFY` for too-short input only |
| 2 | Brain: decides what to remember / goal updates | **PARTIAL** | Capture subscriber stores the exchange; no goal system |
| 13 | LLM gateway: tiering, streaming, structured output, metrics, replaceable provider | **COMPLETE** | `llm/`; `test_llm_gateway.py`, `test_ollama_provider.py`, `RecordedProvider` |
| 3 | Episodic memory: capture, novelty gate, decay maths, tombstones | **COMPLETE** | `memory/capture.py`, `store.py`; `test_memory_*` |
| 3 | Memory decay and forgetting **in the running system** | **COMPLETE** (was BROKEN) | Scheduled nightly as `memory.maintenance` (MEM-01); `test_memory_maintenance_scheduled.py` |
| 3 | Semantic memory (beliefs) | **PARTIAL** | Store, supersession, promotion exist and are tested; **nothing writes beliefs in the live loop** — extraction belongs to reflection, which does not exist |
| 3 | Working memory | **PARTIAL** | Recent-turn window + `working_set_log`; no task-scoped working memory |
| 3 | Relationship memory | **PARTIAL** | `relation` tallies and session-end update; preferences not extracted |
| 3 | Project memory | **MISSING** | No schema or module |
| 3 | Consolidation / duplicates / conflicts | **PARTIAL** | Novelty gate reinforces lexical near-duplicates; `supersede_belief` exists but nothing detects conflicts |
| 3 | User-controlled deletion, searchable, explainable | **PARTIAL** | `tombstone`/`restore`/`pin` in the store; no API or CLI; no `/explain` |
| 3 | Embedding retrieval channel | **MISSING** | Deferred, docs/06 §13.2 |
| 4 | Personality: traits, identity core, guarded drift, reversal | **COMPLETE** | `personality/`; `test_personality_*`, `test_personality_in_the_turn.py` |
| 4 | Personality: evidence-based drift *proposals* | **MISSING** | The proposer is reflection's job |
| 5 | Emotion: six drives, appraisal, decay, mood dynamics, behaviour bindings | **COMPLETE** | `emotion/`; ADR-0019 (excitement/calmness are derived arousal/valence) |
| 5 | Emotion: never manipulative / dependent | **PARTIAL** | Prohibition tests exist for bindings; no honesty eval yet |
| 6 | Curiosity engine | **MISSING** | docs/11 designed; no code |
| 7 | Knowledge: local docs, web search, provenance, citations | **MISSING** | docs/13 designed; tool layer stubbed |
| 7 | Knowledge: "remember vs. researched vs. uncertain" | **PARTIAL** | `TrustTier` and provenance on memory rows; no knowledge tiers |
| 8 | Avatar: VRM renderer, expression from emotion, blink, saccade, breath, head drift | **COMPLETE** | `frontend/src/panes/avatar/`; `idle.test.ts`, `compose.test.ts`, `test_expression.py` |
| 8 | Avatar: speaking / thinking / attention states | **MISSING** | `action` is always idle — the backend never tells the avatar a turn is running |
| 8 | Avatar: lip sync | **MISSING** | Needs voice |
| 8 | Avatar: frames over WebSocket | **MISSING** | Polls `/v1/mind/expression` (acceptable for now, ADR-0021) |
| 9 | Voice: TTS, ASR, streaming, interruption | **MISSING** | — |
| 10 | Reflection (T1–T4) | **MISSING** | docs/12 designed; no code |
| 11 | Goals | **MISSING** | docs/08 §6 designed; no code |
| 13 | Security: loopback bind, CORS, log redaction | **COMPLETE** | `api/app.py`, `core/logging.py` |
| 13 | Security: API token | **COMPLETE** (was MISSING) | `core/token.py`, `api/auth.py`; `test_token_file.py`, `test_conversation_api.py` (ADR-0022) |
| 13 | Security: injection defence | **MISSING** | Not needed until untrusted content enters (knowledge, curiosity) |
| 13 | Observability: correlation ids, `llm_call`, `working_set_log`, turn record | **COMPLETE** | `test_turn_memory_loop.py` |
| 13 | Observability: `/explain`, traces, `/metrics` | **MISSING** | Records exist; the assembly does not |

### 3.3 Integration gaps, debt and documentation drift

| # | Gap | Kind | Story |
|---|---|---|---|
| G1 | No conversation surface at all | Integration | Fixed: FND-01, FND-02, UX-01, UX-02 |
| G2 | Memory maintenance never scheduled | Broken wiring | Fixed by MEM-01 |
| G3 | Beliefs never written outside tests | Incomplete workflow | REF-01 |
| G4 | Avatar unaware of turn activity (thinking/speaking) | Integration | AVA-01 |
| G5 | `README.md` still said "Milestone 1 … no memory, cognition or model yet" | Doc drift | Fixed with FND-02 |
| G6 | docs/16 §3 said the token is required on *every* request; `routes/mind.py` documented a deliberate exception | Doc inconsistency | Resolved by ADR-0022 |
| G7 | No session end from a client — only the idle reaper ends sessions | Incomplete workflow | FND-03 |
| G8 | Concurrent turns on one session are not serialised | Latent bug | FND-04 |
| G9 | Any custom-validator rejection made the 422 handler crash into a 500 (`ctx` held a `ValueError`) | Bug | Fixed with FND-02 (`api/errors.py`) |
| G10 | With `gemma4:e2b`, replies open with a meta-commentary ("That lands as…") and end with a clarifying question every time | Quality | PER-03 |
| G11 | With `gemma4:e2b`, a correctly recalled memory (the right episode, cited) can still be ignored: "It is difficult for me to know what tea you like", with a stray `[Caveat: …]` echoed from the prompt | Quality | PER-03 |
| G12 | The first `compose.reply` after a backend start took 75 s on `gemma4:e2b` (later calls 1–12 s). The non-streaming UI shows only "thinking…" for all of it | Latency | FND-05; investigate warm-up |
| G13 | `VITE_HEDWIG_API` both moved the Vite proxy and was exposed to the page, so setting it bypassed the proxy (and the token it adds) | Config trap | Fixed with UX-02: the proxy also reads `HEDWIG_API_BASE_URL` |

---

## 4. Backlog

Every story carries: ID, status, priority (P0 blocks use · P1 core value · P2 depth · P3
later), complexity (S ≤ ½ day · M ≈ 1–2 days · L ≈ a week · XL needs splitting), dependencies,
affected modules, the story, acceptance criteria and testing requirements.

### 4.1 Foundation

#### FND-01 — Local API bearer token
**Status:** Done (2026-11-20) · **P0** · **S** · **Depends on:** — · **Modules:** `api/auth.py`, `core/config`, `cli`

As the user, I want HEDWIG's API to reject requests that do not carry a token only my
account can read, so that other processes and browser tabs on my machine cannot read my
conversations.

- 32 bytes of CSPRNG, generated at first run at `<data_dir>/token`, mode `0600`.
- Required on every user-content route; health and HEDWIG's own mind state stay open (ADR-0022).
- `Authorization: Bearer` only; constant-time comparison; never logged.
- `hedwig token show` / `hedwig token rotate`.
- **Tests:** missing / wrong / malformed token → 401 `unauthorized`; file permissions are `0600`;
  token survives restart; rotation invalidates the old token.

#### FND-02 — Conversation REST API
**Status:** Done (2026-11-20) · **P0** · **M** · **Depends on:** FND-01 · **Modules:** `api/routes/conversation.py`, `core/ports/sessions.py`, `sessions/store.py`

As a client (CLI, desktop UI, future mobile app), I want to open a session, send a message and
read the history over HTTP, so that a person can actually talk to HEDWIG.

- `POST /v1/sessions` → `{session_id}`; `POST /v1/sessions/{id}/messages` → the complete reply
  with `turn_id`, `message_id`, `status`, `intent`, `cited_memory_ids`, `mood`.
- `GET /v1/sessions/{id}/messages?limit=&before=` — cursor pagination, oldest first.
- A model outage is a `status: "failed"` turn with a 503 `capability_unavailable`, never a 500.
- Text capped at 32 KB; empty text rejected.
- **Tests:** end-to-end against a real container with the recorded provider; a second message
  recalls the first; history paginates; unknown session → 404; outage → 503.

#### FND-03 — Explicit session end
**Status:** Todo · **P1** · **S** · **Depends on:** FND-02 · **Modules:** `sessions/`, `api/`

As the user, I want closing a conversation to end the session immediately, so that the
relationship update (and later T1 reflection) runs then rather than after the idle timeout.

- `POST /v1/sessions/{id}/end`; idempotent; emits `conversation.session.ended` once.
- **Tests:** ending twice emits once; relationship tally closes.

#### FND-04 — One turn at a time per session
**Status:** Todo · **P1** · **S** · **Depends on:** FND-02 · **Modules:** `brain/graph.py`

As the system, I want concurrent messages on one session to run one after another, so that
two turns never interleave their windows.

- Per-session lock owned by `Brain`, not the API edge (the edge has no domain logic).
- **Tests:** two concurrent `run_turn` calls on one session complete in order; different
  sessions run in parallel.

#### FND-05 — WebSocket stream
**Status:** Todo · **P1** · **L** · **Depends on:** FND-02 · **Modules:** `api/stream.py`, `brain/`, `frontend/src/transport/`

As the desktop UI, I want one authenticated WebSocket carrying token streaming, turn stages,
mind updates and notices, so that replies appear as they are written and the avatar can react.

- docs/16 §5 envelope; lossy vs. non-lossy queues; `client_message_id` dedupe; reconnect replay.
- Token via `Sec-WebSocket-Protocol`, not the query string.
- **Tests:** full lifecycle; flood test (tokens never dropped, frames are); duplicate send.

#### FND-06 — Idempotent message send
**Status:** Todo · **P2** · **S** · **Depends on:** FND-02 · **Modules:** `sessions/`, migration

As a client on a flaky connection, I want retrying a send to return the original turn, so that a
retry never makes HEDWIG answer twice.

### 4.2 Core cognition

#### COG-01 — Real guard
**Status:** Todo · **P1** · **M** · **Depends on:** — · **Modules:** `brain/guard.py`

As the system, I want inputs classified for trust and injection before any node reads them, so
that untrusted content can never instruct HEDWIG (ADR-0011).

- Deterministic heuristics first (docs/13 §6 layers 1–3); model scoring optional.
- **Tests:** red-team fixtures; benign corpus false-positive rate < 2 %.

#### COG-02 — Tool registry and the Phase-2 tool set
**Status:** Todo · **P1** · **L** · **Depends on:** COG-01 · **Modules:** `tools/`, `brain/`

As HEDWIG, I want `get_datetime`, `calculate`, `search_memory` to be real tools with schema
validation and a `tool_call` audit row, so that the planner's decisions have effects.

#### COG-03 — Approval interrupt
**Status:** Todo · **P2** · **M** · **Depends on:** COG-02, FND-05 · **Modules:** `brain/`, `api/stream.py`

As the user, I want to approve side-effectful tool calls before they run, surviving a restart.

#### COG-04 — Knowledge-need routing
**Status:** Todo · **P1** · **M** · **Depends on:** COG-02, KNW-02 · **Modules:** `brain/planner.py`

As HEDWIG, I want to decide that a question needs current information (dates, "latest",
version numbers, low recall scores), so that I search instead of guessing.

### 4.3 Memory

#### MEM-01 — Schedule memory maintenance
**Status:** Done (2026-11-20) · **P0** · **S** · **Depends on:** — · **Modules:** `wiring.py`, `memory/maintenance.py`

As HEDWIG, I want decay and forgetting to run nightly in the real process, so that trivia fades
and the store stays bounded — today it only runs in tests (G2).

- Daily trigger at a configurable local hour; 2 %/night cap already enforced by the pass.
- **Tests:** the task is registered when the scheduler is enabled; a run decays and reports.

#### MEM-02 — Memory API and CLI: search, show, pin, forget, restore
**Status:** Todo · **P1** · **M** · **Depends on:** FND-01 · **Modules:** `api/routes/memory.py`, `cli`

As the user, I want to see what HEDWIG remembers, and pin, forget or restore any of it, so that
memory is under my control and explainable.

- docs/16 §4.2 subset; score breakdown on search.
- **Tests:** forget hides from recall in the next turn; restore brings it back.

#### MEM-03 — Memory correction (`PATCH /memory/{id}`)
**Status:** Todo · **P2** · **M** · **Depends on:** MEM-02, REF-01

As the user, I want to correct a wrong memory so that the correction supersedes it with USER trust.

#### MEM-04 — Embedding retrieval channel
**Status:** Todo · **P2** · **L** · **Depends on:** — · **Modules:** `memory/retrieval.py`, migration

As HEDWIG, I want semantic recall over embeddings (sqlite-vec) fused by RRF, so that paraphrases
are recalled. Requires the recall eval corpus first (MEM-05).

#### MEM-05 — Recall eval corpus and baseline
**Status:** Todo · **P1** · **M** · **Depends on:** — · **Modules:** `tests/evals/`

As a developer, I want recall@5 measured on a fixed corpus, so that retrieval changes are judged
by numbers.

#### MEM-06 — Project memory
**Status:** Todo · **P2** · **L** · **Depends on:** REF-01, GOA-01

As the user, I want HEDWIG to track my ongoing projects — decisions, progress, open questions —
so that "how did that project go?" has a real answer. Design question first: an entity kind plus
beliefs, or its own table (ADR).

### 4.4 Personality

#### PER-01 — Personality API
**Status:** Todo · **P2** · **S** · **Depends on:** FND-01 · **Modules:** `api/routes/mind.py`

As the user, I want to see traits, drift history and revert a drift, so that HEDWIG's identity is
inspectable and reversible (docs/16 §4.3).

#### PER-03 — Reply quality on small models: voice and use of memory
**Status:** Todo · **P1** · **M** · **Depends on:** UX-01 · **Modules:** `llm/prompts.py`, `personality/rendering.py`

As the user, I want HEDWIG to answer naturally and to *use* what it recalled — no narration of what
my message "lands as", no reflexive "does that clarify…?", no "I don't know what tea you like" with
the answer in its context — so that memory is felt rather than merely stored. G10, G11.

- Find which rendered directive or prompt line produces each habit; fix at the source. Look first
  at how recalled memories are framed (the `[Caveat: …]` echo).
- **Tests:** prompt snapshot; a recorded-fixture eval of ten replies with zero meta-openers; a
  live eval script (`scripts/eval_models.py` exists) asserting recalled facts are used.

#### PER-02 — Drift proposals from reflection evidence
**Status:** Todo · **P2** · **M** · **Depends on:** REF-02 · **Modules:** `reflection/`, `personality/`

### 4.5 Emotion

#### EMO-01 — Honesty eval
**Status:** Todo · **P2** · **M** · **Depends on:** UX-01

As the user, I want proof that no emotional extreme makes HEDWIG claim real feelings, guilt-trip,
or encourage dependence (INV-10).

#### EMO-02 — Mind state over the stream
**Status:** Todo · **P2** · **S** · **Depends on:** FND-05 · `mind.update` frames replacing polling.

### 4.6 Knowledge and web

#### KNW-01 — Local document ingestion
**Status:** Todo · **P2** · **L** · **Depends on:** COG-02 · **Modules:** `knowledge/`

As the user, I want to point HEDWIG at a folder of notes and have them retrievable with
attribution, with secrets skipped.

#### KNW-02 — Web search and fetch, allowlisted and audited
**Status:** Todo · **P1** · **L** · **Depends on:** COG-01, COG-02 · **Modules:** `knowledge/fetch.py`

As HEDWIG, I want to search the web and read pages through an allowlist, robots, caching,
sanitisation and an egress audit log, so that I can answer current questions without leaking
private data. **Requires the user's go-ahead on the search provider** (a free local option such as
SearXNG vs. a hosted API) before implementation.

#### KNW-03 — Citations and knowledge tiers in replies
**Status:** Todo · **P1** · **M** · **Depends on:** KNW-02

As the user, I want replies to say whether something is remembered, read in a document, just
researched, or uncertain, with sources.

### 4.7 Curiosity

#### CUR-01 — Gap register (offline)
**Status:** Todo · **P2** · **M** · **Depends on:** REF-01 · **Modules:** `curiosity/`

As HEDWIG, I want to record knowledge gaps from conversations (unanswered questions, low-recall
topics), so that curiosity has something to pursue — works with the network off.

#### CUR-02 — Budgeted exploration
**Status:** Todo · **P2** · **L** · **Depends on:** CUR-01, KNW-02 · Off by default (ADR-0012).

#### CUR-03 — Findings feed and interruption policy
**Status:** Todo · **P2** · **M** · **Depends on:** CUR-02, FND-05

### 4.8 Reflection

#### REF-01 — T1 session reflection: summary and belief extraction
**Status:** Todo · **P1** · **L** · **Depends on:** FND-03 · **Modules:** `reflection/`

As HEDWIG, I want to summarise a session when it ends and extract candidate beliefs about the
user through the utility model, so that semantic memory is finally populated (G3).

- Structured output against a schema; capture gates unchanged; idempotent per session.
- **Tests:** recorded-provider extraction; running twice changes nothing.

#### REF-02 — T2 nightly consolidation
**Status:** Todo · **P2** · **XL** (split before starting) · **Depends on:** REF-01, MEM-01

### 4.9 Goals

#### GOA-01 — Goal store and lifecycle
**Status:** Todo · **P2** · **M** · **Depends on:** FND-01 · **Modules:** `goals/`, migration

As HEDWIG, I want goals with priority, status, context, created date, progress and completion
conditions, so that ongoing projects and follow-ups persist (docs/08 §6).

#### GOA-02 — Goals in the turn
**Status:** Todo · **P2** · **M** · **Depends on:** GOA-01 · Goal relevance in retrieval and salience; user request always outranks background goals.

### 4.10 Avatar

#### AVA-01 — Thinking and speaking states from turn activity
**Status:** Todo · **P1** · **S** · **Depends on:** FND-02 · **Modules:** `expression/`, `api/routes/mind.py`, `frontend/src/panes/avatar/`

As the user, I want the avatar to look thoughtful while HEDWIG composes and animate while it
replies, so that the face reflects what HEDWIG is doing, not only how it feels.

#### AVA-02 — Text-paced mouth movement
**Status:** Todo · **P2** · **S** · **Depends on:** AVA-01 · Fallback lip motion before TTS exists.

#### AVA-03 — Phoneme lip sync
**Status:** Todo · **P3** · **M** · **Depends on:** VOI-01

### 4.11 Voice

#### VOI-01 — Local TTS adapter (Piper)
**Status:** Todo · **P2** · **L** · **Depends on:** FND-05 · **Modules:** `voice/`

As the user, I want HEDWIG to speak replies locally with a configurable voice and rate.
**Requires the user's go-ahead** on the dependency (Piper binary + voice model download).

#### VOI-02 — Local ASR (whisper.cpp), opt-in
**Status:** Todo · **P3** · **L** · **Depends on:** VOI-01

#### VOI-03 — Interruptions
**Status:** Todo · **P3** · **M** · **Depends on:** VOI-01, FND-05

### 4.12 User experience

#### UX-01 — `hedwig chat`
**Status:** Done (2026-11-20) · **P0** · **S** · **Depends on:** FND-02 · **Modules:** `cli.py`, `client.py`

As the user, I want to talk to HEDWIG from a terminal, so that it is usable today without the UI.

- Talks to the running server through the public API only (docs/17 §9, CLI parity).
- Reads the token from the data directory; resumes a session with `--session`.
- Clear message when the server is not running.
- **Tests:** against an in-process app; error rendering; `/quit`.

#### UX-02 — Conversation pane in the desktop app
**Status:** Done (2026-11-20) · **P0** · **M** · **Depends on:** FND-02 · **Modules:** `frontend/src/panes/conversation/`, `electron/preload.ts`, `vite.config.ts`

As the user, I want to chat with HEDWIG beside its avatar, so that opening the app means talking
to it. Token delivered by the Electron preload and, in browser dev, injected by the Vite proxy
(ADR-0022).

#### UX-03 — Mind Inspector
**Status:** Todo · **P2** · **L** · **Depends on:** MEM-02, PER-01

### 4.13 Security and reliability

#### SEC-01 — `/explain/{message_id}`
**Status:** Todo · **P2** · **M** · **Depends on:** FND-02 · Assembly of existing records (docs/16 §6).

#### SEC-02 — Export and purge
**Status:** Todo · **P2** · **L** · **Depends on:** MEM-02 · Purge-completeness test (docs/21 §7).

#### SEC-03 — Backup and `hedwig doctor`
**Status:** Todo · **P3** · **M**

---

## 5. Next up

Dependency order, highest value first:

1. ~~UX-02~~, ~~MEM-01~~ — done
2. **PER-03** — reply quality on small models (the most visible problem now that it is usable)
3. **FND-05** — streaming, which also answers G12
4. **FND-03**, **FND-04** — session end, per-session turn lock (small, unblock REF-01)
5. **AVA-01** — the avatar reacts to turns
6. **MEM-02** — memory control for the user
7. **REF-01** — semantic memory populated by reflection
8. **COG-01** → **COG-02** → **KNW-02** (needs a provider decision) → **KNW-03** → **COG-04**
9. **FND-05** — WebSocket, then **VOI-01** (needs a dependency decision)

---

## 6. Development log

### 2026-11-20 — Session 1

**Inspected:** everything listed in §2. 883 tests passing at the start.

**Completed**

- **FND-01** API bearer token — `core/token.py`, `api/auth.py`, `hedwig token show|rotate`.
  [ADR-0022](adr/0022-the-token-gates-user-content.md).
- **FND-02** Conversation REST API — `api/routes/conversation.py`, `ConversationHistory` port,
  `Brain.open_session`. Design in [16](16-api-structure.md) §12.
- **UX-01** `hedwig chat` — `client.py` (HTTP only, enforced by an import-linter contract),
  `cli.py`.
- **MEM-01** Nightly memory maintenance — `memory.maintenance` task, `DailyTrigger` at
  `memory.maintenance_hour` (default 03:00 local). Fixes G2: decay and forgetting had never run
  outside tests.
- Fixed in passing: G5 (README status), G6 (token scope), G9 (422 handler crash).

**Tests:** 926 passed (43 new: `test_token_file.py`, `test_conversation_api.py`,
`test_cli_chat.py`, `test_memory_maintenance_scheduled.py`). mypy, ruff, import-linter (4 contracts) clean.

**Verified live** against Ollama `gemma4:e2b` on a throwaway data directory: 401 without the
token, token file `0600`, a two-turn `hedwig chat`, and a *new* session recalling the first
session's fact through memory (2 memories cited).

**Known issues:** G8 (concurrent turns on one session — the UI sends one at a time), no retry
dedupe (FND-06), G10/G11 (reply quality), G12 (cold first reply). The uncommitted `reasoning`/`think` change to the LLM gateway that was in the working
tree at the start belongs to the user and was left uncommitted.

- **UX-02** Conversation pane — `frontend/src/panes/conversation/`; the API token reaches the
  renderer through Electron IPC and, in browser development, the Vite proxy, both by asking the
  CLI. Design in [18](18-frontend-architecture.md) §13. Fixed G13 in passing.

**Frontend tests:** 75 passed (16 new: the pane, the composer, the token in the transport).

**Verified live in the browser pane** against `gemma4:e2b` on a scratch data directory: the
pane connects through the proxy with the token, shows the message optimistically and
"thinking…", renders the reply, resumes the conversation after a reload, starts over with *New
conversation*, and shows "drew on 1 memory" for a cross-session question. That run surfaced G11
and G12.

**Next:** PER-03 — make the small model use what memory hands it.
