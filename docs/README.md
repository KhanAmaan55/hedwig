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
| 09 | [Emotion Engine](09-emotion-engine.md) | Appraisal, state integration, decay, behaviour bindings |
| 10 | [Personality Engine](10-personality-engine.md) | Trait vector, evidence-based drift, guardrails, identity core |
| 11 | [Curiosity Engine](11-curiosity-engine.md) | Knowledge-gap detection, budgeted exploration, interruption policy |
| 12 | [Reflection Engine](12-reflection-engine.md) | Five reflection tiers, consolidation, idempotency |
| 13 | [Knowledge and Tools](13-knowledge-and-tools.md) | The four knowledge tiers, tool layer, provenance, injection defence |
| 14 | [Language Model Gateway](14-language-model-gateway.md) ✅ | Provider abstraction, model tiering, prompt assembly, structured output |
| 15 | [Avatar Controller](15-avatar-controller.md) | Expression intent, frame protocol, renderer independence, lip sync |
| 16 | [API Structure](16-api-structure.md) | REST resources, WebSocket protocol, versioning, auth |
| 17 | [Backend Architecture](17-backend-architecture.md) | Process model, worker supervision, resource governance, startup/shutdown |
| 18 | [Frontend Architecture](18-frontend-architecture.md) | Panes, state flow, avatar rendering, Mind Inspector |
| 19 | [Observability](19-observability.md) | Tracing, correlation, metrics, the explainability endpoint |
| 20 | [Testing Strategy](20-testing-strategy.md) | Determinism harness, property tests, behaviour evals, red-team suite |
| 21 | [Security, Privacy and Ethics](21-security-privacy-ethics.md) | Threat model, trust tiers, honesty invariants, export and deletion |
| 22 | [Development Roadmap](22-roadmap.md) | Eight phases with exit criteria |
| 23 | [Challenged Assumptions and Open Questions](23-challenged-assumptions.md) | Where the README's premises are wrong or underspecified, and the tradeoff register |
| 24 | [Decision Records](24-decision-records.md) | ADR-0001 … ADR-0018, the decisions the rest of the docs assume |
| 25 | [Core Infrastructure](25-core-infrastructure.md) ✅ | What Milestone 2 built: the seven platform services, their ports, and how they start |
| 26 | [The Turn/Memory Loop](26-turn-memory-loop.md) ✅ | What Milestone 6 built: observe → retrieve → inject → response context → store → persist |

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
| Retrieval: semantic/embedding channel | **Deferred** — [06](06-memory-architecture.md) §13.2 | — |
| Conversation log: sessions, messages, turns, working-set log | **Implemented** (M6) | `src/hedwig/sessions/` |
| The turn/memory loop: capture, reinforcement, persistence | **Implemented** (M6) | [26](26-turn-memory-loop.md) |
| Brain collaborators: guard, mind, responder, tools | **Stubbed, and they say so** | `src/hedwig/brain/stubs.py` |
| API: `/v1/health`, error envelope, correlation middleware | **Implemented** (M1) | `src/hedwig/api/` |
| API: conversation endpoints, WebSocket, auth token | Not started | — |
| CLI: `serve`, `config`, `models`, `version` | **Implemented** (M1, M3) | `src/hedwig/cli.py` |
| Frontend shell + transport + health view | **Implemented** (M1) | `frontend/` |
| Electron desktop shell | **Implemented** (M1, [ADR-0015](adr/0015-electron-desktop-shell.md)) | `electron/` |
| Architecture tests (layering, clock seam, port purity) | **Implemented** (M1) | `tests/architecture/` |
| Reflection, emotion, personality, curiosity, goals, tools, avatar | Not started | — |

The milestone numbering above follows the phases in [22-roadmap.md](22-roadmap.md);
Milestone 1 corresponds to Phase 0.

## How to change architecture

1. Open (or amend) an ADR in [24-decision-records.md](24-decision-records.md) — from
   Phase 1 onward, each new decision gets its own file under `docs/adr/NNNN-title.md`.
2. Update the affected subsystem documents, including their diagrams.
3. Update [02-system-architecture.md](02-system-architecture.md) if the module map or a
   dependency rule changed.
4. Only then change code.
