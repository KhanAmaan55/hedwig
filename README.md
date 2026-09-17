# HEDWIG

Hybrid Evolving Digital Wisdom & Intelligence Guardian

## Vision

HEDWIG is not a chatbot.

It is a persistent digital companion that grows alongside its user through memory, curiosity, reflection, personality, and long-term learning.

Unlike traditional assistants that answer questions and forget, HEDWIG remembers experiences, develops relationships, learns user preferences, reflects on past interactions, and proactively discovers knowledge.

The language model is only one component of the system.

The intelligence comes from the interaction of multiple cognitive systems.

---

## Core Principles

- Memory instead of conversation history
- Personality instead of system prompts
- Emotions instead of fixed responses
- Curiosity instead of passive waiting
- Reflection instead of forgetting
- Goals instead of single-turn conversations
- Local-first architecture
- Privacy by default
- Modular design

---

## Philosophy

Observe

↓

Understand

↓

Remember

↓

Learn

↓

Reflect

↓

Grow

---

## Major Components

### Brain

LangGraph orchestrates all decision making.

Responsible for:

- planning
- routing
- memory retrieval
- tool execution
- reflection

---

### Language

Ollama provides local language models.

Models can be swapped without changing the architecture.

---

### Memory

Long-term memory

Short-term memory

Working memory

Semantic memory

Relationship memory

---

### Emotion Engine

Maintains internal emotional state.

Examples

- curiosity
- happiness
- trust
- confidence
- stress
- energy

Emotions influence behaviour but are not real feelings.

---

### Personality Engine

Persistent personality traits.

Examples

- humour
- patience
- curiosity
- empathy
- confidence

Traits evolve over time.

---

### Curiosity Engine

When idle, HEDWIG can:

- discover new information
- read documentation
- monitor AI news
- summarize interesting findings
- update internal knowledge

---

### Reflection Engine

Runs periodically.

Responsible for

- summarizing conversations
- strengthening memories
- forgetting irrelevant information
- updating relationships
- updating goals

---

### Knowledge

Knowledge is divided into:

- Internal Memory
- Local Documents
- Live Internet
- Current Conversation

---

### Avatar

An expressive digital character.

Emotion controls

- facial expressions
- posture
- eye movement
- idle animations
- lip sync

The avatar reflects internal state rather than directly following LLM outputs.

---

## Long-Term Goal

Create a believable digital companion that develops a consistent identity through memory, reflection, curiosity, and interaction.

The objective is not to simulate consciousness.

The objective is to create continuity.

---

## Architecture

The full architecture lives in [`docs/`](docs/README.md). Code is downstream of those
documents: when the architecture changes, the documents change in the same commit.

Start here:

- [Vision and Scope](docs/01-vision-and-scope.md) — the principles above, restated as testable invariants, plus explicit non-goals
- [System Architecture](docs/02-system-architecture.md) — layers, module map, dependency rules, technology choices
- [Module Contracts](docs/03-module-contracts.md) — the ports that make every module replaceable
- [Development Roadmap](docs/22-roadmap.md) — eight phases with binary exit criteria
- [Challenged Assumptions](docs/23-challenged-assumptions.md) — where this document's premises were corrected, and what is still open

Subsystem documents cover memory, the LangGraph brain, state management, the emotion,
personality, curiosity and reflection engines, the avatar controller, knowledge and tools,
the API, backend and frontend, observability, testing, and security and ethics. The index
is in [`docs/README.md`](docs/README.md).

**Status:** Milestone 1 (Phase 0 — Project Foundation) is complete. Structure, seams and the
desktop shell exist; no memory, cognition or model yet. What is and is not implemented is
tracked in [`docs/README.md`](docs/README.md#implementation-status).

---

## Running it

Requires Python 3.12+, Node 22.12+, and [uv](https://docs.astral.sh/uv/).

```bash
npm run setup   # uv sync + npm install
npm run dev     # backend + renderer + desktop shell, one log stream
```

`npm run dev` starts the backend (`127.0.0.1:8730`), the Vite dev server
(`localhost:5173`) and the Electron shell, in that order, waiting for each to be healthy
before starting the next. Both sides hot reload. Ctrl-C stops everything.

| Command | What it does |
|---|---|
| `npm run dev` | The whole stack |
| `npm run dev -- --no-electron` | Backend and renderer only; open `http://localhost:5173` |
| `npm run check` | Format, lint, typecheck and test — everything CI runs |
| `npm test` | Python and frontend suites |
| `uv run hedwig serve` | Backend alone |
| `uv run hedwig config` | The effective configuration after all layers resolve |

Configuration layers, lowest precedence first: defaults in code → `config/hedwig.toml` →
`config/hedwig.local.toml` → `.env` → `HEDWIG_*` environment variables → CLI flags. Copy
[`.env.example`](.env.example) to `.env` for machine-local overrides.

---