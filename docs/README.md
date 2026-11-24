# HEDWIG Architecture Documentation

This directory is the authoritative description of HEDWIG's architecture. Code is
downstream of these documents. When architecture changes, these documents change in
the same commit as the code.

## Reading order

Read 01 → 04 before anything else. They contain the rules that every other document
depends on. After that, read whatever subsystem you are working on.

| # | Document | What it answers |
|---|---|---|
| 01 | [Vision and Scope](01-vision-and-scope.md) | What we are building, what we are explicitly not building, which principles are testable |
| 02 | [System Architecture](02-system-architecture.md) | Layers, module map, dependency rules, repository layout, technology choices |
| 03 | [Module Contracts](03-module-contracts.md) | The ports every module is written against; how a module gets replaced |
| 04 | [Communication and Event Bus](04-communication-and-event-bus.md) | Command vs. event rule, envelope format, full event catalogue |
| 05 | [Data Model and Database](05-data-model-and-database.md) ◐ | Every table, ERD, migrations, storage engines |
| 06 | [Memory Architecture](06-memory-architecture.md) ◐ | Memory types, capture → consolidate → retrieve → decay → forget |
| 07 | [Brain and LangGraph Workflow](07-brain-langgraph-workflow.md) ✅ | The graph, node catalogue, subgraphs, interrupts, checkpointing |
| 08 | [State Management](08-state-management.md) | The four state tiers, ownership rules, consistency, recovery |
| 09 | [Emotion Engine](09-emotion-engine.md) ✅ | Appraisal, state integration, decay, behaviour bindings |
| 10 | [Personality Engine](10-personality-engine.md) ✅ | Trait vector, evidence-based drift, guardrails, identity core |
| 11 | [Curiosity Engine](11-curiosity-engine.md) ◐ | Knowledge-gap detection, budgeted exploration, interruption policy |
| 12 | [Reflection Engine](12-reflection-engine.md) ◐ | Five reflection tiers, consolidation, idempotency |
| 13 | [Knowledge and Tools](13-knowledge-and-tools.md) ◐ | The four knowledge tiers, tool layer, provenance, injection defence |
| 14 | [Language Model Gateway](14-language-model-gateway.md) ✅ | Provider abstraction, model tiering, prompt assembly, structured output |
| 15 | [Avatar Controller](15-avatar-controller.md) ◐ | Expression intent, frame protocol, renderer independence, lip sync |
| 16 | [API Structure](16-api-structure.md) ◐ | REST resources, WebSocket protocol, versioning, auth |
| 17 | [Backend Architecture](17-backend-architecture.md) | Process model, worker supervision, resource governance, startup/shutdown |
| 18 | [Frontend Architecture](18-frontend-architecture.md) ◐ | Panes, state flow, avatar rendering, Mind Inspector |
| 19 | [Observability](19-observability.md) | Tracing, correlation, metrics, the explainability endpoint |
| 20 | [Testing Strategy](20-testing-strategy.md) | Determinism harness, property tests, behaviour evals, red-team suite |
| 21 | [Security, Privacy and Ethics](21-security-privacy-ethics.md) | Threat model, trust tiers, honesty invariants, export and deletion |
| 22 | [Development Roadmap](22-roadmap.md) | Eight phases with exit criteria |
| 23 | [Challenged Assumptions and Open Questions](23-challenged-assumptions.md) | Where the README's premises are wrong or underspecified, and the tradeoff register |
| 24 | [Decision Records](24-decision-records.md) | ADR-0001 … ADR-0025, the decisions the rest of the docs assume |
| 25 | [Core Infrastructure](25-core-infrastructure.md) ✅ | What Milestone 2 built: the seven platform services, their ports, and how they start |
| 26 | [The Turn/Memory Loop](26-turn-memory-loop.md) ✅ | What Milestone 6 built: observe → retrieve → inject → response context → store → persist |
| 27 | [Personality, Relationship and the Prompt](27-personality-relationship-and-the-prompt.md) ✅ | What Milestone 9 built: traits and their brakes, the relationship, and the prompt the model receives |
| 28 | [Avatar MVP](28-avatar-mvp.md) ✅ | What Milestone 10 built: a VRM avatar that reacts only to emotional state |
| 29 | [Capability Assessment and Backlog](29-backlog.md) | Where HEDWIG stands against the vision, and the story backlog work is picked from |

## Document conventions

Every subsystem document has the same skeleton, so a reader can jump to the section
they need without reading the whole file:

1. **Purpose** — one paragraph.
2. **Responsibilities** and **Non-responsibilities** — the second is as important as the first.
3. **Interfaces** — the ports it provides and consumes, and the events it publishes/subscribes.
4. **Design** — diagrams and mechanism.
5. **Data** — what it owns in the database.
6. **Tradeoffs** — what we gave up, and what we would need to see to change our mind.
7. **Failure modes** — what breaks and what happens then.
8. **Testing** — how this subsystem is verified.
9. **Future improvements** — deferred work, with the trigger that should un-defer it.

Type signatures in these documents are **contracts, not implementations**. They exist
to pin down module boundaries. They are written in Python typing syntax because that is
the precise notation available; they are not the code.

## Implementation status

Individual document headers still read **Design**; this table is the single accurate map
of what exists in code. Update it as each milestone lands.

| Area | Status | Where |
|---|---|---|
| Platform: config, clock, ids, logging, correlation, errors | **Implemented** (M1) | `src/hedwig/core/` |
| Event bus, config manager, plugin loader, state, registry, scheduler, file storage | **Implemented** (M2) | `src/hedwig/core/`, [25](25-core-infrastructure.md) |
| Composition root / DI | **Implemented** (M1) | `src/hedwig/wiring.py` |
| Database, migrations, schema | **Implemented** (M2, M5, M6) | `src/hedwig/core/store/`, `migrations/` |
| Language model gateway: tiering, streaming, structured output, metrics | **Implemented** (M3) | `src/hedwig/llm/` |
| Brain: graph, nodes, routing, planner, caps | **Implemented** (M4) | `src/hedwig/brain/` |
| Memory: episodic, semantic, importance, decay, forgetting | **Implemented** (M5) | `src/hedwig/memory/` |
| Retrieval: lexical, structural, temporal channels | **Implemented** (M5) | `src/hedwig/memory/retrieval.py` |
| Memory control: `/v1/memory`, `hedwig memory` (list, search, pin, forget, restore) | **Implemented** (MEM-02, [06](06-memory-architecture.md) §16) | `src/hedwig/api/routes/memory.py` |
| Voice: talk by microphone (local faster-whisper), stop a reply mid-sentence, mouth shapes from the words | **Implemented** (VOI-02, VOI-03, AVA-03, [15](15-avatar-controller.md) §13.1–13.2) | `src/hedwig/speech/whisper.py`, `frontend/src/voice/` |
| Live channel: idempotent sends, pushed mind state, model warm-up | **Implemented** (FND-06, EMO-02, G12, [16](16-api-structure.md) §13.1, [14](14-language-model-gateway.md) §15) | `src/hedwig/api/routes/stream.py`, `src/hedwig/brain/graph.py` |
| Nightly checked backups, offline restore, `hedwig doctor` | **Implemented** (SEC-03, [05](05-data-model-and-database.md) §7) | `src/hedwig/core/store/backups.py`, `src/hedwig/doctor.py` |
| Project memory: goals with a journal; finished projects recalled when named | **Implemented** (MEM-06, [08](08-state-management.md) §13, [ADR-0025](adr/0025-projects-are-goals-with-a-journal.md)) | `src/hedwig/goals/`, Goals tab *Journal* |
| Local documents: notes recalled with the file named, secrets skipped | **Implemented** (KNW-01, [13](13-knowledge-and-tools.md) §14) | `src/hedwig/knowledge/documents.py`, Data tab *Your documents* |
| Personality drift from the user's reactions, guarded and revertable | **Implemented** (PER-02, [10](10-personality-engine.md) §13) | `src/hedwig/personality/feedback.py`, Mind tab *Recent changes* |
| Memory correction: the user's word replaces, the old version stays as history | **Implemented** (MEM-03, [06](06-memory-architecture.md) §18) | `src/hedwig/memory/store.py`, inspector *Correct* |
| Corrections in conversation: "No, she lives in Lisbon" proposes a correction the user confirms | **Implemented** (MEM-07, [06](06-memory-architecture.md) §19) | `src/hedwig/memory/corrections.py`, conversation pane |
| Retrieval: semantic/embedding channel; recall eval (recall@5 0.306 → 0.972) | **Implemented** (MEM-04, MEM-05, [06](06-memory-architecture.md) §17) | `src/hedwig/memory/embeddings.py`, `tests/evals/`, `scripts/eval_recall.py` |
| Conversation log: sessions, messages, turns, working-set log | **Implemented** (M6) | `src/hedwig/sessions/` |
| The turn/memory loop: capture, reinforcement, persistence | **Implemented** (M6) | [26](26-turn-memory-loop.md) |
| Emotion: six dimensions, deterministic transitions, behaviour bindings | **Implemented** (M7, [ADR-0019](adr/0019-the-emotion-vector-is-six-named-drives.md)) | `src/hedwig/emotion/` |
| Cognition in the turn: snapshot, directives, correlated events, `emotion_ref` | **Implemented** (M8) | [07](07-brain-langgraph-workflow.md) §14 |
| API: `/v1/mind/emotion` and its history | **Implemented** (M7) | `src/hedwig/api/routes/mind.py` |
| Personality: ten traits, identity core, guarded drift, instruction fast path, reversal | **Implemented** (M9) | `src/hedwig/personality/` |
| Relationship memory, session end | **Implemented** (M9; relationship trust frozen without a signal) | `src/hedwig/memory/relationship.py` |
| Prompt assembly and the model-backed responder | **Implemented** (M9) | `src/hedwig/llm/prompts.py`, `responder.py` |
| Brain collaborators: guard, tools | **Implemented** (COG-01/02, [13](13-knowledge-and-tools.md) §12) | `src/hedwig/brain/guard.py`, `src/hedwig/tools/`, `src/hedwig/knowledge/injection.py` |
| API: `/v1/health`, error envelope, correlation middleware | **Implemented** (M1) | `src/hedwig/api/` |
| API: bearer token on user-content routes | **Implemented** (FND-01, [ADR-0022](adr/0022-the-token-gates-user-content.md)) | `src/hedwig/core/token.py`, `src/hedwig/api/auth.py` |
| API: sessions and messages (REST, non-streaming) | **Implemented** (FND-02, [16](16-api-structure.md) §12) | `src/hedwig/api/routes/conversation.py` |
| API: session end; one turn at a time per session | **Implemented** (FND-03, FND-04; [07](07-brain-langgraph-workflow.md) §16) | `src/hedwig/brain/graph.py`, `src/hedwig/api/routes/conversation.py` |
| API: WebSocket stream (tokens, stages, completion), REST fallback in the app | **Implemented** (FND-05, [16](16-api-structure.md) §13, [ADR-0023](adr/0023-live-turn-output-through-a-scoped-sink.md)) | `src/hedwig/api/routes/stream.py`, `src/hedwig/core/live.py`, `frontend/src/transport/socket.ts` |
| API: `/v1/explain` and *Why?* in the app | **Implemented** (SEC-01, [16](16-api-structure.md) §14) | `src/hedwig/explain.py` |
| API: traces and metrics; the doctor's last week | **Implemented** (OBS-01, [19](19-observability.md) §13) | `src/hedwig/core/tracing.py`, `src/hedwig/telemetry/` |
| API: feedback, replay on reconnect | Not started | — |
| CLI: `serve`, `chat`, `token`, `config`, `models`, `version` | **Implemented** (M1, M3, UX-01) | `src/hedwig/cli.py`, `src/hedwig/client.py` |
| Frontend shell + transport + health view | **Implemented** (M1) | `frontend/` |
| Electron desktop shell | **Implemented** (M1, [ADR-0015](adr/0015-electron-desktop-shell.md)) | `electron/` |
| Architecture tests (layering, clock seam, port purity) | **Implemented** (M1) | `tests/architecture/` |
| Avatar: VRM renderer, expression resolver, `/v1/mind/expression` | **Implemented** (M10, [ADR-0021](adr/0021-vrm-first-semantic-seam-kept.md)) | `src/hedwig/expression/`, `frontend/src/panes/avatar/` |
| Avatar thinking/speaking states; voice (Piper) with amplitude mouth | **Implemented** (AVA-01, VOI-01, AVA-02, [15](15-avatar-controller.md) §13) | `src/hedwig/speech/`, `frontend/src/voice/`, `frontend/src/stores/activity.ts` |
| Mind Inspector: mood, relationship, personality, memory, goals, curiosity | **Implemented** (UX-03, PER-01, [18](18-frontend-architecture.md) §14) | `frontend/src/panes/inspector/`, `src/hedwig/api/routes/persona.py` |
| Avatar: 2D renderer, WebSocket frames, phoneme lip sync, gestures; speech recognition | Not started | — |
| Memory maintenance scheduled nightly in the running process | **Implemented** (MEM-01; a stage of the nightly run since REF-02) | `src/hedwig/reflection/nightly.py`, `src/hedwig/memory/maintenance.py` |
| Frontend conversation pane, token delivery (Electron IPC, Vite proxy) | **Implemented** (UX-02, [18](18-frontend-architecture.md) §13) | `frontend/src/panes/conversation/`, `electron/` |
| Reflection T1: session summary and belief extraction | **Implemented** (REF-01, [12](12-reflection-engine.md) §14) | `src/hedwig/reflection/` |
| Goals: store, lifecycle, API/CLI, in the prompt, from reflection | **Implemented** (GOA-01/02, [08](08-state-management.md) §6.1) | `src/hedwig/goals/` |
| Web research: opt-in search, fetch controls, citations, egress audit | **Implemented** (KNW-02/03, [13](13-knowledge-and-tools.md) §13) | `src/hedwig/knowledge/`, `src/hedwig/tools/research.py` |
| Curiosity: gap register, gated exploration, findings feed, deferred surfacing | **Implemented** (CUR-01/02/03, [11](11-curiosity-engine.md) §13) | `src/hedwig/curiosity/` |
| Speaking first: goal-linked findings and check-ins, live, at most one a day | **Implemented** (INI-01, [11](11-curiosity-engine.md) §14, ADR-0026) | `src/hedwig/initiative/`, `src/hedwig/core/presence.py` |
| Export everything; purge with derivation-following and file-level verification | **Implemented** (SEC-02, [21](21-security-privacy-ethics.md) §7.4, [ADR-0024](adr/0024-purge-is-one-classified-sweep.md)) | `src/hedwig/privacy/`, inspector *Data* tab |
| Reflection T2 nightly: day summaries, belief merge and promotion, judged conflicts put to the user | **Implemented** (REF-02a/b/c, [12](12-reflection-engine.md) §15) | `src/hedwig/reflection/nightly.py`, `frontend/src/panes/inspector/` |
| Reflection T3 weekly review and T4 monthly identity report | **Implemented** (REF-03, [12](12-reflection-engine.md) §16) | `src/hedwig/reflection/periodic.py`, `frontend/src/panes/inspector/` |
| Reflection T2 procedures; T3 entity resolution | Not started — [29](29-backlog.md) | — |

The milestone numbering above follows the phases in [22-roadmap.md](22-roadmap.md);
Milestone 1 corresponds to Phase 0.

## How to change architecture

1. Open (or amend) an ADR in [24-decision-records.md](24-decision-records.md) — from
   Phase 1 onward, each new decision gets its own file under `docs/adr/NNNN-title.md`.
2. Update the affected subsystem documents, including their diagrams.
3. Update [02-system-architecture.md](02-system-architecture.md) if the module map or a
   dependency rule changed.
4. Only then change code.
