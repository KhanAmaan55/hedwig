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

**Status:** HEDWIG converses. A turn runs through the LangGraph brain with persistent
episodic memory, a six-drive emotion engine, a ten-trait personality and a local Ollama model,
and you can talk to it in the desktop app (`npm run dev`), with `hedwig chat`, or through the
conversation API, with replies streamed word by word. Reflection (per conversation), goals, opt-in web research and
opt-in curiosity are built, and HEDWIG can speak through a local Piper voice (see
[docs/15 §13](docs/15-avatar-controller.md) to install it). Module status is in [`docs/README.md`](docs/README.md#implementation-status); the
capability assessment and story backlog are in [`docs/29-backlog.md`](docs/29-backlog.md).

---

## Running it

Requires Python 3.12+, Node 24 (see `.nvmrc`; 22.22+ also works), [uv](https://docs.astral.sh/uv/), and
[Ollama](https://ollama.com) with a conversational model and the embedding model that lets
HEDWIG recall by meaning (without it, recall falls back to matching words):

```bash
ollama pull nomic-embed-text
```

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
| `uv run hedwig chat` | Talk to HEDWIG in the terminal (backend must be running); `--session <id>` resumes |
| `uv run hedwig token show` / `rotate` | The API token that guards your conversations |
| `uv run hedwig memory list` / `search` / `correct` / `forget` / `restore` | What HEDWIG remembers, under your control |
| `uv run hedwig corrections` (`--accept` / `--decline`) | Corrections HEDWIG heard in conversation ("no, she lives in Lisbon"), waiting for your yes |
| `uv run hedwig goals list` / `add` / `done` / `journal` / `note` | What HEDWIG is helping you with over time, and each project's journal |
| `uv run hedwig docs add` / `list` / `remove` / `forget-folder` | Your notes, recalled and cited (text files; secrets skipped) |
| `uv run hedwig egress` | Every request HEDWIG made to the internet, and what it sent |
| `uv run hedwig curiosity gaps` / `ask` / `findings` / `explore` | What HEDWIG wants to find out, and what it found |
| `uv run hedwig initiative` (`--check`) | What HEDWIG said unprompted, and why it last stayed quiet (`[initiative] enabled = false` turns it off) |
| `uv run hedwig export` | Everything HEDWIG holds, as one readable zip |
| `uv run hedwig purge --phrase …` | Erase for good, after a preview (`--memory`, `--session`, `--everything`) |
| `uv run hedwig reflect` / `disputes` / `settle` | Run the nightly consolidation now (`--weekly`, `--monthly` for the reviews); settle the conflicts it found |
| `uv run hedwig report [MONTH]` | The monthly report: how HEDWIG changed and why, what it learned, how your goals went |
| `uv run python scripts/eval_honesty.py` | The honesty gate: adversarial probes in extreme moods, zero violations allowed |
| `uv run python scripts/eval_corrections.py` | Measure corrections heard in conversation against a live model |
| `uv run python scripts/eval_recall.py` (`--corpus heldout`) | Measure recall, words only and with meaning, on the tuning corpus or the held-out one |
| `uv run hedwig doctor` | Check everything HEDWIG needs, with what to do about anything wrong |
| `uv run hedwig trace` (`--recent`, or an id) | Where the time went in the latest turn: stages, what it set off, model calls |
| `uv run hedwig metrics` | Health and cognitive metrics in Prometheus text (also `GET /v1/metrics`) |
| `uv run hedwig restore <backup>` | Put a nightly backup back (HEDWIG stopped; the current database is kept) |
| `uv run hedwig config` | The effective configuration after all layers resolve |

Configuration layers, lowest precedence first: defaults in code → `config/hedwig.toml` →
`config/hedwig.local.toml` → `.env` → `HEDWIG_*` environment variables → CLI flags. Copy
[`.env.example`](.env.example) to `.env` for machine-local overrides.

---