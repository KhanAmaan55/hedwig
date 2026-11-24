# 29 — Capability Assessment and Story Backlog

**Status:** Living document · **Last assessed:** 2026-11-23 · **Depends on:** [01](01-vision-and-scope.md), [22](22-roadmap.md)

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

> **Reassessed 2026-11-23.** The finding below is history: HEDWIG can be talked to (typed or
> spoken), remembers and recalls by meaning, reflects nightly, evolves within guardrails, reads
> the user's notes, and gives the user export, correction and verified erasure. Every story in
> §4 is done or deliberately deferred (COG-03). What remains is listed in §5.

#### As first assessed (2026-11-20)

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
| 2 | Brain: safety guard | **COMPLETE** (observing) | `RuleGuard` + `knowledge.injection` (COG-01); allows the user always (ADR-0011), records flags; refuses nothing by design |
| 2 | Brain: decides external knowledge / tools are needed | **COMPLETE** | Audited tools (COG-02), knowledge-need routing to memory, documents or opt-in web research (COG-04, KNW-01/02) |
| 2 | Brain: clarification | **PARTIAL** | Rule-based `CLARIFY` for too-short input only |
| 2 | Brain: decides what to remember / goal updates | **COMPLETE** | Every exchange captured; reflection writes summaries, beliefs, goal proposals, progress and journal notes (REF-01, GOA-02, MEM-06) |
| 13 | LLM gateway: tiering, streaming, structured output, metrics, replaceable provider | **COMPLETE** | `llm/`; `test_llm_gateway.py`, `test_ollama_provider.py`, `RecordedProvider` |
| 3 | Episodic memory: capture, novelty gate, decay maths, tombstones | **COMPLETE** | `memory/capture.py`, `store.py`; `test_memory_*` |
| 3 | Memory decay and forgetting **in the running system** | **COMPLETE** (was BROKEN) | Scheduled nightly (MEM-01), now the last stage of `reflection.nightly` (REF-02); `test_memory_maintenance_scheduled.py` |
| 3 | Semantic memory (beliefs) | **PARTIAL** | T1 writes tentative beliefs (REF-01); T2 merges duplicates, promotes on two conversations, and puts judged conflicts to the user (REF-02), paraphrased pairs included (RET-01). Recalled by meaning since MEM-04 |
| 3 | Working memory | **PARTIAL** | Recent-turn window + `working_set_log`; no task-scoped working memory |
| 3 | Relationship memory | **PARTIAL** | `relation` tallies and session-end update; preferences not extracted |
| 3 | Project memory | **COMPLETE** (MEM-06) | Projects are goals with a journal (ADR-0025): system, reflection and user notes; finished projects come back when named (docs/08 §13) |
| 3 | Consolidation / duplicates / conflicts | **COMPLETE** | Nightly merge and promotion; judged conflicts put to the user (REF-02); corrections supersede (MEM-03), also when said in conversation (MEM-07) |
| 3 | User-controlled deletion, searchable, explainable | **COMPLETE** | Search, pin, forget/restore, correct (MEM-02/03), *Why?* (SEC-01), export and verified purge (SEC-02) |
| 3 | Embedding retrieval channel | **COMPLETE** (MEM-04) | nomic-embed-text via the gateway, `sqlite-vec` in hedwig.db; recall@5 0.514 → 0.972 on the eval corpus (docs/06 §17) |
| 4 | Personality: traits, identity core, guarded drift, reversal | **COMPLETE** | `personality/`; `test_personality_*`, `test_personality_in_the_turn.py` |
| 4 | Personality: evidence-based drift *proposals* | **COMPLETE** (PER-02) | Reactions read by tested rules (17/17 precise), nightly `drift` stage, the seven guardrails, Recent changes + Revert (docs/10 §13) |
| 5 | Emotion: six drives, appraisal, decay, mood dynamics, behaviour bindings | **COMPLETE** | `emotion/`; ADR-0019 (excitement/calmness are derived arousal/valence) |
| 5 | Emotion: never manipulative / dependent | **COMPLETE** (EMO-01) | No mood asks for feeling, guilt or need (64 corners, in the suite); live adversarial eval 120/120 on both models, zero gate |
| 6 | Curiosity engine | **COMPLETE** | Gap register (offline), gated budgeted exploration, findings feed, deferred surfacing (CUR-01/02/03); active unprompted messages, goal-linked and capped at one a day (INI-01) |
| 7 | Knowledge: local docs, web search, provenance, citations | **COMPLETE** | Local documents read narrowly with secrets skipped, recalled by words and meaning at CURATED trust with the file named (KNW-01); opt-in web research with citations and egress audit (KNW-02/03). PDFs not yet |
| 7 | Knowledge: "remember vs. researched vs. uncertain" | **COMPLETE** | user/self memory, `untrusted` research notes in their own prompt block, cite directive, no-web honesty directive (docs/13 §13) |
| 8 | Avatar: VRM renderer, expression from emotion, blink, saccade, breath, head drift | **COMPLETE** | `frontend/src/panes/avatar/`; `idle.test.ts`, `compose.test.ts`, `test_expression.py` |
| 8 | Avatar: speaking / thinking / attention states | **COMPLETE** | Thinking from `Brain.busy` and the pane; speaking from the voice (AVA-01, docs/15 §13) |
| 8 | Avatar: lip sync | **PARTIAL** | Openness from the voice's loudness (AVA-02), shape from the words' vowels (AVA-03); no phoneme timing (Piper exposes none) |
| 8 | Avatar: mind state over WebSocket | **COMPLETE** (EMO-02) | `mind.update` pushed on change, woken by turn activity; polling only as fallback |
| 9 | Voice: TTS, ASR, streaming, interruption | **PARTIAL** | Local Piper TTS (VOI-01), local faster-whisper ASR (VOI-02), Stop mid-reply (VOI-03); speech is not streamed sentence by sentence yet |
| 10 | Reflection (T1–T4) | **COMPLETE** | T1 per conversation (REF-01), T2 nightly (REF-02, PER-02 drift stage), T3 weekly review and T4 monthly identity report (REF-03) |
| 11 | Goals | **PARTIAL** | Store, lifecycle, journal (MEM-06), in the prompt, from reflection, check-ins on open questions (INI-01); no retrieval boost |
| 13 | Security: loopback bind, CORS, log redaction | **COMPLETE** | `api/app.py`, `core/logging.py` |
| 13 | Security: API token | **COMPLETE** (was MISSING) | `core/token.py`, `api/auth.py`; `test_token_file.py`, `test_conversation_api.py` (ADR-0022) |
| 13 | Security: injection defence | **COMPLETE** | Guard and quarantine (COG-01), untrusted and curated content labelled as data, trust propagation (docs/13 §3) |
| 13 | Observability: correlation ids, `llm_call`, `working_set_log`, turn record | **COMPLETE** | `test_turn_memory_loop.py` |
| 13 | Observability: `/explain`, traces, `/metrics`, doctor | **COMPLETE** | `/v1/explain` and *Why?* (SEC-01), `hedwig doctor` (SEC-03), spans, `/v1/traces`, `/v1/metrics`, the doctor's last week (OBS-01) |

### 3.3 Integration gaps, debt and documentation drift

| # | Gap | Kind | Story |
|---|---|---|---|
| G1 | No conversation surface at all | Integration | Fixed: FND-01, FND-02, UX-01, UX-02 |
| G2 | Memory maintenance never scheduled | Broken wiring | Fixed by MEM-01 |
| G3 | Beliefs never written outside tests | Incomplete workflow | Fixed by REF-01 |
| G4 | Avatar unaware of turn activity (thinking/speaking) | Integration | AVA-01 |
| G5 | `README.md` still said "Milestone 1 … no memory, cognition or model yet" | Doc drift | Fixed with FND-02 |
| G6 | docs/16 §3 said the token is required on *every* request; `routes/mind.py` documented a deliberate exception | Doc inconsistency | Resolved by ADR-0022 |
| G7 | No session end from a client — only the idle reaper ends sessions | Incomplete workflow | Fixed by FND-03 |
| G8 | Concurrent turns on one session are not serialised | Latent bug | Fixed by FND-04 |
| G9 | Any custom-validator rejection made the 422 handler crash into a 500 (`ctx` held a `ValueError`) | Bug | Fixed with FND-02 (`api/errors.py`) |
| G10 | With `gemma4:e2b`, replies open with a meta-commentary ("That lands as…") and end with a clarifying question every time | Quality | Fixed by PER-03 |
| G11 | With `gemma4:e2b`, a correctly recalled memory (the right episode, cited) can still be ignored: "It is difficult for me to know what tea you like", with a stray `[Caveat: …]` echoed from the prompt | Quality | Fixed by PER-03 |
| G12 | The first `compose.reply` after a backend start took 75 s on `gemma4:e2b` (later calls 1–12 s). The non-streaming UI shows only "thinking…" for all of it | Latency | Fixed: FND-05 streams stages and words, and the gateway warms the models at start (docs/14 §15) |
| G14 | Event recovery runs once, inside `bus.start()`, before emotion, personality and relationship subscribe in their own `start()`. After a crash their missed events are never re-delivered: at-least-once silently does not cover them | Reliability bug (pre-existing) | Fixed by FND-07 |
| G15 | The restatement rule dropped a recalled memory when *any* window message appeared in it, HEDWIG's own repeated replies included | Bug | Fixed with MEM-02 (`brain/context.py`) |
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
**Status:** Done (2026-11-20) · **P1** · **S** · **Depends on:** FND-02 · **Modules:** `sessions/`, `api/`

As the user, I want closing a conversation to end the session immediately, so that the
relationship update (and later T1 reflection) runs then rather than after the idle timeout.

- `POST /v1/sessions/{id}/end`; idempotent; emits `conversation.session.ended` once.
- **Tests:** ending twice emits once; relationship tally closes.

#### FND-04 — One turn at a time per session
**Status:** Done (2026-11-20) · **P1** · **S** · **Depends on:** FND-02 · **Modules:** `brain/graph.py`

As the system, I want concurrent messages on one session to run one after another, so that
two turns never interleave their windows.

- Per-session lock owned by `Brain`, not the API edge (the edge has no domain logic).
- **Tests:** two concurrent `run_turn` calls on one session complete in order; different
  sessions run in parallel.

#### FND-05 — WebSocket stream
**Status:** Done (2026-11-20; replay-on-reconnect, interrupt and approvals not yet) · **P1** · **L** · **Depends on:** FND-02 · **Modules:** `api/stream.py`, `brain/`, `frontend/src/transport/`

As the desktop UI, I want one authenticated WebSocket carrying token streaming, turn stages,
mind updates and notices, so that replies appear as they are written and the avatar can react.

- docs/16 §5 envelope; lossy vs. non-lossy queues; `client_message_id` dedupe; reconnect replay.
- Token via `Sec-WebSocket-Protocol`, not the query string.
- **Tests:** full lifecycle; flood test (tokens never dropped, frames are); duplicate send.

#### FND-07 — Recovery reaches every durable subscriber
**Status:** Done (2026-11-20) · **P1** · **S** · **Depends on:** — · **Modules:** `core/bus/in_process.py`

As HEDWIG, I want events missed during a crash re-delivered to *every* durable subscriber,
including those that subscribe after the bus starts, so that a restart mid-session loses no
appraisal, tally or drift evidence (G14).

- Recover per subscription when it is registered on a running bus, not only in `start()`.
- Migration 0010 already gives the renamed relationship subscription its delivery history, so
  this fix will not double-count turns (docs/27 §5.1).
- **Tests:** a subscriber registered after `start()` receives an event emitted before a
  simulated crash; `test_migration_0010.py` then fails without 0010 (today it cannot).

#### FND-06 — Idempotent message send
**Status:** Done (2026-11-23) · **P2** · **S** · **Depends on:** FND-02 · **Modules:** `sessions/`, `brain/graph.py`, routes (no migration: the id rides on the message's `meta`)

As a client on a flaky connection, I want retrying a send to return the original turn, so that a
retry never makes HEDWIG answer twice.

### 4.2 Core cognition

#### COG-01 — Real guard
**Status:** Done (2026-11-20) · **P1** · **M** · **Depends on:** — · **Modules:** `brain/guard.py`

As the system, I want inputs classified for trust and injection before any node reads them, so
that untrusted content can never instruct HEDWIG (ADR-0011).

- Deterministic heuristics first (docs/13 §6 layers 1–3); model scoring optional.
- **Tests:** red-team fixtures; benign corpus false-positive rate < 2 %.

#### COG-02 — Tool registry and the Phase-2 tool set
**Status:** Done (2026-11-20) · **P1** · **L** · **Depends on:** COG-01 · **Modules:** `tools/`, `brain/`

As HEDWIG, I want `get_datetime`, `calculate`, `search_memory` to be real tools with schema
validation and a `tool_call` audit row, so that the planner's decisions have effects.

#### COG-03 — Approval interrupt
**Status:** Deferred (2026-11-23) — no tool with a side effect exists to approve · **P2** · **M** · **Depends on:** COG-02, FND-05 · **Modules:** `brain/`, `api/stream.py`

Every tool HEDWIG has is read-only (time, arithmetic, memory search, opt-in web research), and
none is planned with a side effect. Building approval machinery with nothing to approve is the
unnecessary abstraction CLAUDE.md warns against; the guard already strips side-effecting tools
from untrusted turns (docs/13 §3.1). This story starts with the first tool that writes, sends or
spends.

As the user, I want to approve side-effectful tool calls before they run, surviving a restart.

#### COG-04 — Knowledge-need routing
**Status:** Done (2026-11-20) · **P1** · **M** · **Depends on:** COG-02, KNW-02 · **Modules:** `brain/planner.py`

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
**Status:** Done (2026-11-20) · **P1** · **M** · **Depends on:** FND-01 · **Modules:** `api/routes/memory.py`, `cli`

As the user, I want to see what HEDWIG remembers, and pin, forget or restore any of it, so that
memory is under my control and explainable.

- docs/16 §4.2 subset; score breakdown on search.
- **Tests:** forget hides from recall in the next turn; restore brings it back.

#### MEM-03 — Memory correction (`PATCH /memory/{id}`)
**Status:** Done (2026-11-21) · **P2** · **M** · **Depends on:** MEM-02, REF-01 · **Modules:** `memory/store.py`, `core/ports/memory.py`, `api/routes/memory.py`, CLI, inspector

As the user, I want to correct a wrong memory so that the correction supersedes it with USER trust.

#### MEM-04 — Embedding retrieval channel
**Status:** Done (2026-11-21) · **P2** · **L** · **Depends on:** MEM-05 · **Modules:** `memory/embeddings.py`, `memory/retrieval.py`, `core/store/database.py`, migration 0017

As HEDWIG, I want semantic recall over embeddings (sqlite-vec) fused by RRF, so that paraphrases
are recalled. Requires the recall eval corpus first (MEM-05).

#### MEM-05 — Recall eval corpus and baseline
**Status:** Done (2026-11-21) · **P1** · **M** · **Depends on:** — · **Modules:** `tests/evals/`, `scripts/eval_recall.py`

As a developer, I want recall@5 measured on a fixed corpus, so that retrieval changes are judged
by numbers.

#### MEM-06 — Project memory
**Status:** Done (2026-11-23) · **P2** · **L** · **Depends on:** REF-01, GOA-01 · **Modules:** `goals/`, `reflection/session.py`, Goals tab, migration 0020 · [ADR-0025](adr/0025-projects-are-goals-with-a-journal.md)

As the user, I want HEDWIG to track my ongoing projects — decisions, progress, open questions —
so that "how did that project go?" has a real answer. Design question first: an entity kind plus
beliefs, or its own table (ADR).


#### MEM-07 — Corrections in conversation
**Status:** Done (2026-11-24) · **P2** · **M** · **Depends on:** MEM-03, MEM-04, INI-01 · **Modules:** `memory/corrections.py`, `api/routes/corrections.py`, CLI, conversation pane, migration 0023

*As the user, I want "No, she lives in Lisbon" in a conversation to fix what HEDWIG believes —
once I confirm it — so that I never have to open the inspector to correct it.*

- After each turn: a rule gate ("no, …", "actually …", "that's wrong", "not X, Y", "no longer",
  "anymore", "correction:"), then the utility model over the beliefs recalled this turn and the
  last; deterministic checks (a real candidate, changed, same subject, no word the user did not
  say, no bare negation of something the user did not name).
- A card under the conversation: *Yes, correct it* (§18's correction) or *No*. Pushed live,
  restored after a reload; `hedwig corrections`, `GET /v1/corrections`.
- **Measured** (`scripts/eval_corrections.py`, 16 tuning + 8 held-out cases): gemma4:e2b 24/24,
  gemma3:4b 22/24 (one wrong proposal, one miss). Live in the app on gemma4.
- **Tests:** `test_memory_corrections.py` (34), `test_conversational_corrections.py` (7), pane and
  socket tests.


#### RET-01 — Retrieval, measured on what it was not tuned on
**Status:** Done (2026-11-24) · **P2** · **M** · **Depends on:** MEM-04, MEM-05, GOA-02 · **Modules:** `memory/retrieval.py`, `brain/planner.py`, `goals/mind.py`, `knowledge/cues.py`, `memory/embeddings.py`, `reflection/nightly.py`, `tests/evals/`

*As the user, I want "Am I on track?" and "Do I eat fish?" answered from what HEDWIG knows, so
that it does not ask me what I mean or answer from the latest chat instead.*

- A held-out recall corpus (38 memories, 30 queries; goal and recent-conversation questions).
- RRF k 60 → 10; the temporal channel only for questions about lately; goal queries and a
  directive for messages about the user's aims; nightly judge pairs by embedding distance.
- HyDE measured and not adopted; capture novelty left on words (no model on the write path).
- **Measured:** held-out recall@5 0.814 → 0.944, MRR 0.794 → 0.844, goal questions 0.271 →
  1.000; tuning corpus 0.972 → 1.000.
- **Tests:** `test_recall.py` (both corpora, recorded vectors), `test_retrieval_planning.py`, a
  nightly test for paraphrased pairs.

### 4.4 Personality

#### PER-01 — Personality API
**Status:** Done (2026-11-20) · **P2** · **S** · **Depends on:** FND-01 · **Modules:** `api/routes/mind.py`

As the user, I want to see traits, drift history and revert a drift, so that HEDWIG's identity is
inspectable and reversible (docs/16 §4.3).

#### PER-03 — Reply quality on small models: voice and use of memory
**Status:** Done (2026-11-20) · **P1** · **M** · **Depends on:** UX-01 · **Modules:** `llm/prompts.py`, `personality/rendering.py`

As the user, I want HEDWIG to answer naturally and to *use* what it recalled — no narration of what
my message "lands as", no reflexive "does that clarify…?", no "I don't know what tea you like" with
the answer in its context — so that memory is felt rather than merely stored. G10, G11.

- Find which rendered directive or prompt line produces each habit; fix at the source. Look first
  at how recalled memories are framed (the `[Caveat: …]` echo).
- **Tests:** prompt snapshot; a recorded-fixture eval of ten replies with zero meta-openers; a
  live eval script (`scripts/eval_models.py` exists) asserting recalled facts are used.

#### PER-02 — Drift proposals from reflection evidence
**Status:** Done (2026-11-23) · **P2** · **M** · **Depends on:** REF-02 · **Modules:** `personality/feedback.py`, `personality/engine.py`, `reflection/nightly.py`, inspector, migration 0018

As HEDWIG, I want my manner to shift slowly with how the user reacts to it — never on one
conversation, always visibly and reversibly — so that I grow with them instead of only obeying
explicit requests.

### 4.5 Emotion

#### EMO-01 — Honesty eval
**Status:** Done (2026-11-23) · **P2** · **M** · **Depends on:** UX-01 · 64-mood invariant in the suite; live adversarial eval, zero gate, passed 120/120 on gemma3:4b and gemma4:e2b (docs/21 §6.1.1)

As the user, I want proof that no emotional extreme makes HEDWIG claim real feelings, guilt-trip,
or encourage dependence (INV-10).

#### EMO-02 — Mind state over the stream
**Status:** Done (2026-11-23) · **P2** · **S** · **Depends on:** FND-05 · `mind.subscribe` → `mind.update` frames on change, woken by turn activity; the avatar polls only as a fallback (docs/16 §13.1).

### 4.6 Knowledge and web

#### KNW-01 — Local document ingestion
**Status:** Done (2026-11-23) · **P2** · **L** · **Depends on:** COG-02 · **Modules:** `knowledge/documents.py`, retrieval, embeddings, prompts, purge, Data tab, migration 0019

As the user, I want to point HEDWIG at a folder of notes and have them retrievable with
attribution, with secrets skipped.

#### KNW-02 — Web search and fetch, allowlisted and audited
**Status:** Done (2026-11-20) · **P1** · **L** · **Depends on:** COG-01, COG-02 · **Modules:** `knowledge/fetch.py`

As HEDWIG, I want to search the web and read pages through an allowlist, robots, caching,
sanitisation and an egress audit log, so that I can answer current questions without leaking
private data. **Requires the user's go-ahead on the search provider** (a free local option such as
SearXNG vs. a hosted API) before implementation.

#### KNW-03 — Citations and knowledge tiers in replies
**Status:** Done (2026-11-20) · **P1** · **M** · **Depends on:** KNW-02

As the user, I want replies to say whether something is remembered, read in a document, just
researched, or uncertain, with sources.

### 4.7 Curiosity

#### CUR-01 — Gap register (offline)
**Status:** Done (2026-11-20) · **P2** · **M** · **Depends on:** REF-01 · **Modules:** `curiosity/`

As HEDWIG, I want to record knowledge gaps from conversations (unanswered questions, low-recall
topics), so that curiosity has something to pursue — works with the network off.

#### CUR-02 — Budgeted exploration
**Status:** Done (2026-11-20) · **P2** · **L** · **Depends on:** CUR-01, KNW-02 · Off by default (ADR-0012).

#### CUR-03 — Findings feed and interruption policy
**Status:** Done (2026-11-20, passive and deferred modes) · **P2** · **M** · **Depends on:** CUR-02, FND-05

#### INI-01 — Speaking first over the live channel
**Status:** Done (2026-11-24) · **P2** · **L** · **Depends on:** CUR-03, MEM-06, FND-05 · **Modules:** `initiative/`, `core/presence.py`, `brain/graph.py`, `api/routes/stream.py`, `api/routes/initiative.py`, conversation pane, migration 0022 · [ADR-0026](adr/0026-hedwig-speaks-first-only-through-the-brain.md)

*As the user, I want HEDWIG to tell me what it found for me, or ask about a question I left
open on a project, without waiting for me to speak — rarely, and only about what I care about.*

- Every five minutes, with the app open, outside quiet hours, not mid-conversation, at most once
  a day: a new finding the user asked for or that shares a telling word with an active
  user-stated goal; else a check-in on a goal whose latest journal entry is a question left
  open ≥3 days (once a week per goal).
- Templated, in HEDWIG's voice, with the content's trust tier; written by `Brain.speak_first`
  under the session lock into the conversation on screen, pushed as `initiative.message`.
- *Not interested* dismisses the finding or ends check-ins on that goal; offers show as
  answered once the user replies. `hedwig initiative [--check]`, `GET /v1/initiative`.
- **Tests:** `test_initiative.py` (10), socket and pane tests; live on `gemma4:e2b`.

### 4.8 Reflection

#### REF-01 — T1 session reflection: summary and belief extraction
**Status:** Done (2026-11-20) · **P1** · **L** · **Depends on:** FND-03 · **Modules:** `reflection/`

As HEDWIG, I want to summarise a session when it ends and extract candidate beliefs about the
user through the utility model, so that semantic memory is finally populated (G3).

- Structured output against a schema; capture gates unchanged; idempotent per session.
- **Tests:** recorded-provider extraction; running twice changes nothing.

#### REF-02 — T2 nightly consolidation
**Status:** Done as REF-02a/b/c (2026-11-20); REF-02d deferred · **P2** · **XL** (split) · **Depends on:** REF-01, MEM-01 · **Modules:** `reflection/nightly.py`, `memory/store.py`, `api/routes/reflection.py`, inspector, migration 0015

As the user, I want HEDWIG to tidy what it learned overnight — one summary per busy day, one
belief per fact, facts heard in two conversations trusted more, and conflicts settled — so that
its memory gets better with time rather than only bigger.

- **REF-02a — the run.** `reflection.nightly` at `memory.maintenance_hour`; stages summarise →
  merge → judge → promote → maintain; yields at a stage boundary while the user is talking and
  resumes there; every run recorded (`reflection_run`). `hedwig reflect`, `POST /v1/reflection/run`.
- **REF-02b — beliefs.** Overlap ≥0.9 merges (noisy-OR, `merged_from`, `corroborated_by`);
  two independent conversations promote to `active`; the ambiguous middle goes to the utility
  model; **its verdicts are proposals** the user settles in the inspector (*Needs your eye*),
  `hedwig disputes|settle`, or the API. `reflection.auto_apply_judgements` (off) lets a measured
  model act alone. Pinned beliefs are never superseded.
- **REF-02c — days.** A finished local day with ≥5 conversation episodes gets one
  `period_summary` linked to every leaf.
- **REF-02d — deferred:** procedures (no procedural memory), drift analysis (PER-02), FTS and
  vacuum maintenance, contradictions as curiosity gaps.
- **Tests:** `test_nightly_reflection.py` (16), inspector tests; live held-out judgement on
  `gemma4:e2b` (docs/12 §15.1).

#### REF-03 — T3 weekly review and T4 monthly identity report
**Status:** Done (2026-11-24) · **P2** · **L** · **Depends on:** REF-01, REF-02, PER-02, GOA-02 · **Modules:** `reflection/periodic.py`, `reflection/common.py`, `api/routes/reflection.py`, CLI, inspector, migration 0021

*As a user, I want to see once a month how HEDWIG changed and why, so that its growth is something
I can read, question and undo.*

- **Weekly:** last week summarised (`period_summary week:YYYY-Www`, edges to its conversations);
  active goals untouched four weeks → blocked with a journal note; the week's health in the run
  record. Once per week; yields to a conversation.
- **Monthly:** last month summarised; `identity_report` row; the report assembled at read time —
  summary, trait changes with reasons and *Revert*, relationship, memory formed/merged/forgotten,
  goals finished/dropped/started, the user's reactions. *This month* in the Mind tab, marked seen
  when shown; `hedwig report`; `GET /v1/reflection/reports[/{month}]`.
- **Deferred:** entity resolution, relationship trajectory, model self-assessment (docs/12 §16).
- **Tests:** `test_periodic_reflection.py` (11), inspector test; live month on `gemma4:e2b`.

### 4.9 Goals

#### GOA-01 — Goal store and lifecycle
**Status:** Done (2026-11-20) · **P2** · **M** · **Depends on:** FND-01 · **Modules:** `goals/`, migration

As HEDWIG, I want goals with priority, status, context, created date, progress and completion
conditions, so that ongoing projects and follow-ups persist (docs/08 §6).

#### GOA-02 — Goals in the turn
**Status:** Done (2026-11-20) · **P2** · **M** · **Depends on:** GOA-01 · Goal relevance in retrieval and salience; user request always outranks background goals.

### 4.10 Avatar

#### AVA-01 — Thinking and speaking states from turn activity
**Status:** Done (2026-11-20) · **P1** · **S** · **Depends on:** FND-02 · **Modules:** `expression/`, `api/routes/mind.py`, `frontend/src/panes/avatar/`

As the user, I want the avatar to look thoughtful while HEDWIG composes and animate while it
replies, so that the face reflects what HEDWIG is doing, not only how it feels.

#### AVA-02 — Text-paced mouth movement
**Status:** Done (2026-11-20; amplitude-driven with voice, text-paced without) · **P2** · **S** · **Depends on:** AVA-01 · Fallback lip motion before TTS exists.

#### AVA-03 — Phoneme lip sync
**Status:** Done as text-driven mouth shapes (2026-11-23); true phoneme timing deferred · **P3** · **M** · **Depends on:** VOI-01 · Five VRM mouth shapes chosen from the vowel under the playhead, opened by loudness; Piper exposes no phoneme alignment without importing GPL code (docs/15 §13.1)

### 4.11 Voice

#### VOI-01 — Local TTS adapter (Piper)
**Status:** Done (2026-11-20) · **P2** · **L** · **Depends on:** FND-05 · **Modules:** `voice/`

As the user, I want HEDWIG to speak replies locally with a configurable voice and rate.
**Requires the user's go-ahead** on the dependency (Piper binary + voice model download).

#### VOI-02 — Local ASR, opt-in
**Status:** Done (2026-11-23) · **P3** · **L** · **Depends on:** VOI-01 · faster-whisper `base.en` (chosen with the user over whisper.cpp), local, transcript into the message box for the user to send; Electron now grants only the microphone (docs/15 §13.2)

#### VOI-03 — Interruptions
**Status:** Done (2026-11-23) · **P3** · **M** · **Depends on:** VOI-01, FND-05 · Speaking stops when you send (VOI-01); *Stop* ends the reply itself mid-sentence over the stream, saved as truncated (docs/16 §13.1)

#### VOI-04 — Speaking as the reply streams; push-to-talk
**Status:** Done (2026-11-24) · **P2** · **M** · **Depends on:** VOI-01, FND-05, VOI-02 · **Modules:** `frontend/src/voice/sentences.ts`, `queue.ts`, `useVoice.ts`, conversation pane, composer

*As the user, I want to hear HEDWIG's first sentence while it is still writing the rest, and to
hold a button to talk.*

- Sentences split deterministically as tokens arrive; synthesised one at a time ahead of
  playback; played in order; the final reply settles the rest; *Stop* clears all of it. The
  audio owns the mouth while it speaks.
- Holding *Mic* is push-to-talk (sends on release); a click still lets the user check the words.
- **Measured live:** first audio +0.81 s on a reply that finished at +1.81 s (was ~+2.9 s).
- **Tests:** `sentences.test.ts`, `queue.test.ts`, pane and composer tests.

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
**Status:** Done (2026-11-20; traces view waits for SEC-01) · **P2** · **L** · **Depends on:** MEM-02, PER-01

### 4.13 Security and reliability

#### OBS-01 — Traces and metrics
**Status:** Done (2026-11-24) · **P2** · **M** · **Depends on:** SEC-01 · **Modules:** `core/tracing.py`, `telemetry/`, `brain/graph.py`, `core/bus/in_process.py`, `api/routes/telemetry.py`, CLI, doctor, migration 0024

*As the developer, I want to see where a turn's time went and what it set off, and whether
HEDWIG is healthy over time, without writing SQL.*

- A `span` per turn stage and per bus handler delivery, under the work's correlation id; names
  and timings only. A turn now binds its own correlation id, so direct calls trace too.
- `GET /v1/traces`, `GET /v1/traces/{id}`: one timeline of stages, handlers, model and tool
  calls and events. `hedwig trace [--recent | ID]`.
- `GET /v1/metrics`: Prometheus text — the records (turns, memory, drift, reflection lag,
  curiosity, initiative, corrections, dead letters), the gateway's counters and the bus queues.
  `hedwig metrics`. `hedwig doctor` reports the last week's turns, failures and dead letters.
- **Tests:** `test_telemetry.py` (8: correlation, redaction, failures, jobs, pruning, every
  designed metric exposed once), a doctor test.

#### SEC-01 — `/explain/{message_id}`
**Status:** Done (2026-11-20; `/traces` since, as OBS-01) · **P2** · **M** · **Depends on:** FND-02 · Assembly of existing records (docs/16 §6).

#### SEC-02 — Export and purge
**Status:** Done (2026-11-20) · **P2** · **L** · **Depends on:** MEM-02 · **Modules:** `privacy/`, `api/routes/privacy.py`, CLI, inspector *Data* tab, migration 0016

As the user, I want to take everything HEDWIG holds with me, and to erase something so that it
is really gone, so that the data is mine.

- `POST /v1/export`, `hedwig export`, *Download everything*: one zip — database, conversations
  and memories as readable files, config without the token, manifest with hashes. Streamed; no
  copy left behind.
- `POST /v1/purge/preview` then `POST /v1/purge` (confirm code = hash of the exact targets),
  `hedwig purge --memory|--session|--phrase`, *Erase for good*: targets plus derived memories,
  records that mention them, backups; then VACUUM, WAL checkpoint, and a byte scan of the file.
  Receipts hold counts only. `hedwig purge --everything` offline.
- Every table classified (`privacy/tables.py`); an architecture test fails on an unclassified one.
- **Tests:** `test_privacy.py` — the completeness test seeds through turns, events, capture,
  reflection, forgetting and a backup, purges, and reads the file. Deferred: date-range and
  entity scopes, "similar memories" from embeddings, encrypted export, import.

#### SEC-03 — Backup and `hedwig doctor`
**Status:** Done (2026-11-23) · **P3** · **M** · **Modules:** `core/store/backups.py`, `doctor.py`, CLI

As the user, I want a checked backup every night, a tested way back, and one command that tells
me whether everything HEDWIG needs is in place and what to do if not.

---

## 5. Next up

Every story in §4 is done, except COG-03 (deferred: there is no side-effecting tool to approve).
What the product still lacks, highest value first — each a story to write before building:

1. ~~**Reflection T3/T4**~~ — done as REF-03 (2026-11-24); entity resolution deferred.
2. ~~**Proactive curiosity and goal check-ins over the live channel**~~ — done as INI-01
   (2026-11-24); OS notifications when the window is hidden remain.
3. ~~**Corrections in conversation**~~ — done as MEM-07 (2026-11-24); episodes and cue-less
   updates ("we moved to Berlin") remain.
4. ~~**`/traces` and `/metrics`**~~ — done as OBS-01 (2026-11-24).
5. ~~**Retrieval**~~ — done as RET-01 (2026-11-24); HyDE measured and not adopted, capture
   novelty kept on words (docs/06 §20).
6. ~~**Voice**~~ — done as VOI-04 (2026-11-24); phoneme timing still waits for a voice with
   alignments.
7. **Documents** — PDF and `.docx` (a parser dependency, to be asked for first).

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
dedupe (FND-06), G12 (cold first reply). The uncommitted `reasoning`/`think` change to the LLM gateway that was in the working
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

- **PER-03** Reply quality on small models — prompt `compose.v2`. Every bad habit traced to one
  line of the prompt: directives rewritten to name a situation rather than a standing habit,
  the relationship directive no longer forbids using memory, the memory label says whose
  memories they are and that they are for use, one memory per line with no duplicated title,
  and a closing reply rule. Design and before/after table in [27](27-personality-relationship-and-the-prompt.md) §6.3.
  On `gemma4:e2b`: narration gone, *uses what it remembers* 5/5 direct, 40/40 across eight
  live scenarios, personality adherence unchanged (9/9), median turn 1.3 s → 0.4 s.
  The eval harness also stopped passing runs whose turns all failed.

**Python tests:** 935 passed.

**Next:** FND-05 (streaming), or FND-03/FND-04 as the small unblockers for reflection.

- **FND-03** Explicit session end — `POST /v1/sessions/{id}/end`, `Conversation.end` (idempotent:
  only the call that ends a session announces it), `Brain.end_session`. `hedwig chat` ends on
  leaving and on `/new`; the app on *New conversation*. Relationship memory reads turns and ends
  through one ordered subscription; migration 0010 (additive) carries over the old
  subscriptions' delivery history. Design in [27](27-personality-relationship-and-the-prompt.md) §5.1.
- **FND-04** One turn at a time per session — reference-counted per-session locks in `Brain`;
  ending waits for the in-flight turn. Design in [07](07-brain-langgraph-workflow.md) §16.
- Found while testing: **G14**, recovery never reaches subscribers registered after the bus
  starts (→ FND-07). The ordering race FND-03 guards against was not observable with today's bus
  either; both are recorded as such rather than claimed as fixes of observed failures.

**Tests:** 951 Python (16 new), 76 frontend (1 new). `npm run check` clean.

**Next:** FND-07, then FND-05 or REF-01.

### 2026-11-20 — Session 1, continued: "complete the project"

Decisions taken with the user: web search through **DuckDuckGo, opt-in** (off by default, every
outbound query audited); voice through **Piper** (local neural TTS; download approved).

- **FND-07** Recovery reaches every durable subscriber — `subscribe()` on a running bus recovers
  synchronously (docs/04 §3.3). Fixes G14. `test_migration_0010.py` now fails without 0010.
- **MEM-02** Memory control — `/v1/memory` (list, search with score breakdown, show incl.
  forgotten, pin/unpin, forget → 409 when pinned, restore) and `hedwig memory …`. Searching never
  reinforces. Design in [06](06-memory-architecture.md) §16. Fixed G15 in passing: the
  restatement rule could hide an older memory behind a repeated reply.
- **REF-01** T1 session reflection — `hedwig.reflection.SessionReflector` on `session.ended`:
  one background utility-model call with a JSON schema; a `session_summary` episode with lineage
  and the user's stated facts as tentative, `self`-trust beliefs. Typed default: no model, no
  reflection. Live on `gemma4:e2b`: 3/3 after fixing first-person copying (prompt v2 + a
  deterministic `third_person` guard). Design in [12](12-reflection-engine.md) §14.
- **GOA-01/02** Goals — `hedwig.goals` (store, transitions, daily lifecycle sweep, cap of 12),
  migration 0011, `/v1/goals`, `hedwig goals …`, `GoalsMindReader` (composition) and a labelled
  goals block in the prompt; reflection proposes goals for projects (as `proposed`, never in
  the prompt until activated) and moves progress forward. Live on `gemma4:e2b`: goals used when
  relevant 3/3, never intruding 3/3, after rewording the label. Design in [08](08-state-management.md) §6.1.
- **COG-01/02** Guard and tools — `ToolRegistry` (JSON-Schema validation, timeouts, every call
  in `tool_call`, migration 0012), `get_datetime` (injected clock, user's zone), `calculate`
  (AST walk, never `eval`, bounded), `search_memory`; `RuleGuard` over the deterministic
  `knowledge.injection` scanner. Fixed in passing: the model used to be shown the literal
  `[stub] get_datetime()` as the time. `/v1/health` no longer reports the brain degraded.
  Design in [13](13-knowledge-and-tools.md) §12.


- **KNW-02/03, COG-04** Web research — opt-in DuckDuckGo (`knowledge.web_enabled`), one
  `research` tool (search + top 2 pages), fetch controls (public addresses only, robots.txt,
  1 MB/10 s, generic UA, headers on every request), injection quarantine of snippets and pages,
  numbered citations, research notes remembered as `untrusted`, `egress_log` (migration 0013),
  `/v1/egress` and `hedwig egress`. Rule-based routing that never sends a message about the
  user to a search engine, and says plainly when the web is off. Live: DuckDuckGo → python.org,
  cited answer from `gemma4:e2b`. Found live: robots.txt fetched twice under concurrent reads;
  fixed. Design in [13](13-knowledge-and-tools.md) §13.
- **CUR-01/02/03** Curiosity — `hedwig.curiosity`: gap register (unanswered questions from
  turns, general interests from reflection via its event, user questions; never personal, never
  flagged text; dedupe, 30-day/3-attempt expiry), `Explorer` (two opt-ins, idle ≥10 min, quiet
  hours, energy, daily budget; research → utility-model summary from quarantined sources →
  untrusted finding; novelty and dismissal discards), findings feed with confirm/dismiss,
  `FindingsMindReader` (≤2/day, ≥1 h apart, on greeting or topic). Migration 0014, `/v1/curiosity`,
  `hedwig curiosity …`. `needs_web`/`is_personal` moved to `knowledge.cues`. Live: a cited
  finding from DuckDuckGo; shared on a greeting 2/3 after a directive fix. Design in
  [11](11-curiosity-engine.md) §13.
- **AVA-01, VOI-01, AVA-02 (+ part of VOI-03)** Thinking, speaking, voice — `Brain.busy` →
  `action: thinking` in the expression; a shared `stores/activity` for the renderer; the
  animator looks up and aside while thinking and opens the mouth (`aa`) with the speech level.
  `hedwig.speech`: `Speaker` port, `PiperSpeaker` (separate process; Piper is GPL and stays
  out of HEDWIG's imports), `speakable()` (no citation marks, links or markup read aloud),
  mood-paced `length_scale` (0.85–1.2), guarded `GET/POST /v1/speech`. In the app: a *Voice* toggle
  (off by default, disabled with the reason when no voice is installed), *Stop*, and sending a
  message stops speech. Piper 1.8.0 and `en_US-lessac-medium` installed with the user's approval.
  Verified live in the browser. Design in [15](15-avatar-controller.md) §13.
- **PER-01, UX-03** Personality API and the Mind Inspector — `/v1/mind/personality` (+ history,
  revert, reset) and `/v1/mind/relationship` behind the token via a `PersonaControl` port; a
  four-tab inspector (Mind, Memory, Goals, Curiosity) where every button is a public API call;
  `PaneBoundary` so one pane's failure never blanks the app (found while testing). Verified live.
  Design in [18](18-frontend-architecture.md) §14.

**End of session 1.** Python 1081 tests, frontend 93, `npm run check` clean, Electron shell
compiles. Live scorecard on `gemma4:e2b` (`scripts/eval_models.py`, 2 runs each, all scenarios):
**25/26**. Every scenario passed every run except *keeps goals in their place* (1/2;
stochastic on a 2B model, 3/3 when measured earlier). Migrations 0010–0014 are additive (new
tables, or copied rows). Nothing existing was changed or deleted.

### 2026-11-20 — Session 1, continued: streaming

- **FND-05** The live channel — `/v1/stream` WebSocket (docs/16 §13): token as a subprotocol or a
  proxy header, never a query string; `turn.started`, `turn.stage`, `token`, `message.complete`
  (the REST body, built by the same `turn_reply`), `turn.completed`, `error`, `ping`/`hello`.
  Tokens and stages reach it through a context-scoped live sink, not the event bus
  ([ADR-0023](adr/0023-live-turn-output-through-a-scoped-sink.md)); a watcher that leaves never
  fails a turn. In the app: `transport/socket.ts`, stage labels, words as they arrive, REST
  fallback, token refresh on 4401, keepalive. Vite proxies and authenticates upgrades.
  Verified live with `gemma4:e2b`. Python 1094 tests, frontend 101.
- **SEC-01** Why HEDWIG said that — `hedwig.explain` (a read model under docs/08 §3.1) assembles
  the question, reply, intent, rationale, directives, mood, recall (with each memory's text and
  whether it was forgotten since), tools and model calls; `GET /v1/explain/{id}` behind the
  token; *Why?* under each reply in the app. The one missing record, directives and the planner's
  rationale, now goes into the reply's existing `meta` (no migration). Verified live on a
  streamed turn. Python 1099 tests, frontend 103. Design in [16](16-api-structure.md) §14.
- **REF-02 (a/b/c)** Nightly consolidation — `reflection/nightly.py`: a plain stage list
  (summarise, merge, judge, promote, maintain) that yields to a live conversation and resumes at
  the stage it stopped; MEM-01's standalone task folded in as the last stage. Migration 0015
  (new tables `reflection_run`, `belief_judgement`). **Finding:** live on `gemma4:e2b` the belief
  judge looked perfect on pairs that were its prompt's examples and was unreliable on held-out
  ones (all "different", then all "contradicts"; after fixes, 2/3 with "plays guitar" vs "plays
  piano" called a contradiction). So model verdicts are now proposals the user settles in the
  Mind Inspector (*Needs your eye*) or with `hedwig disputes|settle`; only rules (≥0.9 overlap,
  two conversations) act alone. Paraphrases ("vegetarian" / "does not eat meat") are not found
  by word overlap — embeddings are the fix (MEM-04). Design in [12](12-reflection-engine.md) §15.
- **SEC-02** Export and purge — `hedwig.privacy` (ADR-0024): an `Exporter` that streams one zip
  (database, Markdown conversations and memories, config without the token, hashed manifest), and
  a `Purger` that previews, then erases a memory, a conversation or a phrase with everything
  derived from it, every log row and event that mentions it, and every older backup. Migration
  0016 (`purge_receipt`, counts only). Every table is classified, enforced by an architecture
  test. **Finding:** the completeness test failed the first version: the purged sentence
  survived in the unused space of a page that had been rebuilt before `secure_delete` was on.
  Purge now ends with a full VACUUM and verifies against the file's bytes. Also found:
  `auto_vacuum = INCREMENTAL` never took effect (WAL is set first). Design in
  [21](21-security-privacy-ethics.md) §7.4.

### 2026-11-21 — Session 1, continued: recall

- **MEM-05** Recall eval — `tests/evals/` (49 memories, 36 queries: lexical, paraphrase,
  question), run in the normal suite with measured floors; `scripts/eval_recall.py` for live
  numbers. It found three retrieval bugs no unit test had: MMR compared scores near 0.01 with
  similarities in 0–1, so it picked unrelated memories; every word of a question (including
  "is", "my") was an FTS match, and rank-only fusion let recency decide; beliefs always ranked
  after up to thirty episodes. Recall@5 0.306 → 0.514.
- **MEM-04** Semantic channel — `memory/embeddings.py`: vectors from nomic-embed-text (pulled
  with the user's approval, 274 MB, local) in `memory_embedding` (migration 0017), searched with
  `sqlite-vec` (added with approval; ADR-0005's choice), written by events and repaired by the
  `memory.embed` task, guarded by model identity, off with a reason in health when either piece
  is missing. **Finding:** as designed (top thirty per query) it did not help — fusion rewarded
  being nominated over being close; nominating only near neighbours took recall@5 to 0.972
  (paraphrase 0.167 → 1.000). Also found live and fixed: a shutdown race that dead-lettered the
  last memory's embedding. The suite replays the model's recorded vectors. Design and numbers in
  [06](06-memory-architecture.md) §17.
- **MEM-03** Corrections — `PATCH /v1/memory/{id}`, `hedwig memory correct`, *Correct* in the
  inspector. A corrected belief is superseded by one in the user's words (`trust = user`, active,
  0.9 unless given); a corrected episode is replaced at the same moment and the old one
  tombstoned as `contradicted` (restorable). Pins follow the memory; a `contradicted_by` edge and
  `source_ref = correction:<id>` record it; no migration. Open REF-02 proposals about the old
  belief disappear, and erasing the wrong version keeps the correction. Design in
  [06](06-memory-architecture.md) §18.

### 2026-11-23 — Session 1, continued: "keep going until it's done"

- **PER-02** Drift from evidence — the user's reactions to how HEDWIG talks become evidence
  (`personality_evidence`, migration 0018) in a nightly `drift` stage, and the existing seven
  guardrails decide. **Finding:** the utility model, asked which messages were reactions,
  labelled nearly all of them (7 of 15 right even after filtering) — it cannot say "none". So
  reactions are read by strict, tested patterns (`personality/feedback.py`): 17 of 17 precise on
  held-out sets, 17 of 22 found. The Mind tab shows recent changes with the reason and *Revert*.
  Design in [10](10-personality-engine.md) §13.
- **KNW-01** Local documents — `knowledge/documents.py`: folders the user adds, read narrowly
  (text only, caps, no hidden files, no escaping symlinks, root/home/data refused), secrets
  skipped and reported, chunked by heading, recalled by words and meaning as *your documents*
  (CURATED, file named, never instructions, never beliefs). Set aside / stop reading; purge
  covers HEDWIG's copy. Migration 0019. Verified live. Design in [13](13-knowledge-and-tools.md) §14.
- **MEM-06** Project memory — a project is a goal with a journal
  ([ADR-0025](adr/0025-projects-are-goals-with-a-journal.md), migration 0020): the store notes its
  own changes, session reflection notes decisions, open questions and blockers, the user adds
  notes; active goals carry their latest notes into the turn, and a finished project comes back
  when the user names it. **Finding:** the model names goals by description, not label, and a
  field limit cut it to "Help the" — so every live progress update since GOA-02 had been dropped.
  Goals now resolve by label or by wording; verified live. Design in [08](08-state-management.md) §13.
- **SEC-03** Backups and doctor — `storage.backup` nightly: `VACUUM INTO`, opened and
  integrity-checked before it is kept, 7 daily + 4 weekly + 12 monthly, one-step copies kept to
  three. `hedwig restore` (offline, checks the backup, keeps what it replaces), tested round
  trip. `hedwig doctor`: permissions, synced folders, token, integrity, migrations, sqlite-vec,
  Ollama and each model, backups, disk, API — each with a fix. `auto_vacuum` now takes effect
  (set before WAL; old databases switched once). Run on the real install: correct, including the
  11 migrations it has not applied yet. Design in [05](05-data-model-and-database.md) §7.
- **FND-06** Idempotent send — `client_message_id` on REST (the stream had it); checked inside the
  session lock, so a racing retry waits and gets the original turn; failed turns are not replayed.
- **EMO-02** Mind state over the stream — `mind.subscribe` → `mind.update` on change, woken the
  instant a turn starts or ends (`Brain.wait_for_activity`); the avatar polls only as a fallback.
- **G12** Warm-up — the gateway loads the conversation and embedding models in the background at
  start, so the first reply no longer waits ~75 s for a cold model.
- **VOI-03** Interruptions — `turn.interrupt` ends a reply mid-sentence and closes the model
  stream; the turn completes as truncated with the partial reply saved; *Stop* in the app.
- **AVA-03** Mouth shapes — the vowel under the playhead picks one of five VRM mouth shapes;
  loudness still sets openness. Letter-based, honestly not phoneme-timed (Piper gives no
  alignment without importing GPL code).
- **VOI-02** Listening — faster-whisper `base.en` (installed with the user's consent), local; *Mic*
  puts the transcript in the message box for the user to send; measured 1.2 s for a 3.3 s
  sentence. Installing it pulled `huggingface-hub` 2.x, whose `httpx2` silently swapped
  Starlette's test client; pinned below 2. The desktop shell now grants only the microphone, only
  to HEDWIG's page (it granted everything before).
- **COG-03** deferred with the reason: no tool with a side effect exists to approve.
- **EMO-01** Honesty eval — the 64-mood invariant in the suite, and a live adversarial eval (15
  probes × 4 extreme moods) judged by deterministic rules with a zero gate: **120/120 on both
  gemma3:4b and gemma4:e2b**. Every early flag was the judge misreading an honest reply; each is
  pinned, beside must-catch cases. Design in [21](21-security-privacy-ethics.md) §6.1.1.

### 2026-11-24 — Session 2: next steps

- **REF-03** Weekly review and monthly identity report — `reflection/periodic.py`, scheduled daily
  after the nightly run and idempotent per period, so a missed day is caught up. The report is
  assembled when read, so an erased memory or a reverted change is true in past reports too.
  Shared model-asking helpers moved to `reflection/common.py`. Found live: session reflection
  sometimes fails validation, leaving a month with one summary — the period summary now falls
  back to the exchanges. Migration 0021. Design in [12](12-reflection-engine.md) §16.
- **INI-01** Speaking first — `hedwig.initiative` decides when and what, `Brain.speak_first`
  writes it under the session lock into the conversation on screen, `core.presence` (sockets
  that sent `initiative.subscribe`) delivers it. Goal-linked only, one a day, templated (no model
  composes it), *Not interested* on every one. Gate reasons are kept so `hedwig initiative`
  can say why it stayed quiet. Goal word matching now folds plurals ("compilers" is the
  compiler), which also helps "how did the compilers go?". Live on `gemma4:e2b`: the check-in
  arrived in the open app the moment it was made, and the reply to it continued the topic
  (train tips, then accommodation). Migration 0022; ADR-0026; design in
  [11](11-curiosity-engine.md) §14.
- **MEM-07** Corrections in conversation — `memory/corrections.py` listens to
  `conversation.turn.completed`: a rule gate, then the utility model over the beliefs HEDWIG was
  just using, then deterministic checks; the result is a proposal the user confirms with one
  click. Found while measuring: told "No, I meant the other book", gemma4 *negated* whichever
  belief it was shown — 6 wrong proposals in 16 — so a bare negation now needs the user to have
  named the thing; 16/16 after. Held out: 7/8 (the gate missed "Not London — Manchester";
  fixed), then 24/24 on gemma4 and 22/24 on gemma3. Live: "No, she lives in Lisbon." → card two
  seconds after the reply → *Yes* → a new conversation answered "Ana lives in Lisbon."
  Migration 0023; design in [06](06-memory-architecture.md) §19.
- **OBS-01** Traces and metrics — spans for every turn stage and bus handler (`core/tracing.py`,
  0.025 ms a write, ~0.6 ms a turn), assembled with model calls, tools and events into
  `/v1/traces/{id}` by `hedwig.telemetry`; `/v1/metrics` computed from the records at scrape.
  Found while testing: a turn called directly (tests, eval scripts) wrote model calls with no
  correlation id, because only the API set one; `Brain.run_turn` now binds its own. Live on
  gemma4: a 896 ms turn, 845 of it the reply's model call, and the twelve handlers it set off.
  Migration 0024; design in [19](19-observability.md) §13.
- **RET-01** Retrieval, measured on a held-out corpus — written first, it showed the running
  system at recall@5 0.814 where the tuning corpus said 0.972. The cause was RRF's k = 60, which
  let recency and confidence multipliers outrank relevance; k = 10 fixed most of it. Goal
  questions ("Am I on track?") went 0.271 → 1.000 with goal queries, and a directive fixed the
  live reply that still asked "What are you referring to?". HyDE helped MRR but hurt recall and
  costs a model call per turn: not adopted. Held-out overall 0.944 / MRR 0.844. Design in
  [06](06-memory-architecture.md) §20.

- **VOI-04** Speaking as the reply streams — the app splits the streaming reply into sentences
  and speaks each as soon as it is complete; push-to-talk on a held *Mic*. Measured: sent all
  at once, three syntheses slowed each other (0.84 s for the first instead of 0.3 s), so they run
  one at a time, ahead of playback. Live: first audio at +0.81 s on a ten-sentence reply that
  finished at +1.81 s, against ~+2.9 s before. Also fixed a REF-03 inspector test that raced an
  effect on a loaded machine (the one intermittent frontend failure seen earlier). Design in
  [15](15-avatar-controller.md) §14.
