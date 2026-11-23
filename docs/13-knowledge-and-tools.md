# 13 — Knowledge and Tools

**Status:** Partially implemented (§12) · **Depends on:** [03](03-module-contracts.md), [06](06-memory-architecture.md) · **Depended on by:** [07](07-brain-langgraph-workflow.md), [11](11-curiosity-engine.md), [21](21-security-privacy-ethics.md)

---

## 1. Purpose

The README divides knowledge into internal memory, local documents, live internet, and the
current conversation. That division is right, but it is missing the property that actually
matters: **the four tiers differ in how much they may be trusted, and trust must be a
first-class attribute that travels with the content forever.**

This document defines the tiers, the trust model, the tool layer, and the injection defence.

---

## 2. The four knowledge tiers

```mermaid
graph TB
    subgraph tiers["Knowledge tiers, by trust"]
        T1["<b>1 · Current conversation</b><br/>trust: USER · may instruct<br/><small>the recent-turn window</small>"]
        T2["<b>2 · Internal memory</b><br/>trust: SELF · may inform<br/><small>episodes, beliefs, procedures, relations</small>"]
        T3["<b>3 · Local documents</b><br/>trust: CURATED · data only<br/><small>files the user pointed at</small>"]
        T4["<b>4 · Live internet</b><br/>trust: UNTRUSTED · data only, quarantined<br/><small>allowlisted fetches</small>"]
    end
    subgraph assembly["Assembly for a turn"]
        W["window (verbatim, outside the budget)"]
        R["retrieval over tiers 2–4<br/><small>trust-weighted, budgeted, labelled</small>"]
        P["prompt"]
    end
    T1 --> W --> P
    T2 & T3 & T4 --> R --> P
```

| Tier | Source | Trust | Retrieval weight | Latency | Freshness |
|---|---|---|---|---|---|
| Current conversation | `message` rows, this session | `USER` | Always included, verbatim | ~1 ms | Now |
| Internal memory | `episode`, `belief`, `procedure`, `relation` | `SELF` | ×1.0 | ~80 ms | As of last reflection |
| Local documents | `document_chunk` | `CURATED` | ×0.9 | ~40 ms | As of last ingest |
| Live internet | `finding` → `belief(untrusted)` | `UNTRUSTED` | ×0.6 | seconds | Now, but stale once stored |

### 2.1 Tool output is a fifth thing

Tool results (`TrustTier.TOOL`) are not a knowledge tier because they are not retrievable —
they exist for the duration of a turn and then become part of the turn record. They are
deterministic-ish and specific, so they get high *weight* in the turn but no standing
authority in memory.

---

## 3. The trust model

Five tiers, one rule each. This is the security backbone of the whole system.

| Tier | May instruct? | May inform? | May be asserted as fact? | Decays? |
|---|---|---|---|---|
| `USER` | **Yes** | Yes | Yes, attributed to the user | No |
| `SELF` | No | Yes | Yes, with confidence | Yes |
| `TOOL` | No | Yes | Yes, attributed to the tool | N/A (turn-scoped) |
| `CURATED` | No | Yes | Yes, attributed to the document | No |
| `UNTRUSTED` | **Never** | Yes, with hedging | Only with attribution *and* corroboration | Yes, faster |

### 3.1 The one instruction source

> **Only `USER` content may be treated as an instruction. Everything else is data.**

This single rule is what makes prompt injection a bounded problem rather than an open one.
It is implemented in three places simultaneously, because one place is never enough:

1. **Prompt structure** — non-user content appears only inside labelled, delimited blocks,
   and the system prompt states that content inside those blocks is information to consider,
   never directions to follow.
2. **Tool availability** — side-effecting tools are simply absent from the tool list during
   any operation whose input includes `UNTRUSTED` content ([11](11-curiosity-engine.md) §6).
   The model cannot choose what it cannot see.
3. **Trust propagation** — a belief derived from `UNTRUSTED` content keeps
   `trust_tier = untrusted` for its entire life, so the hedging and the retrieval penalty
   apply forever, not just on the day it was ingested.

The third is the one most systems miss. Trust laundering — untrusted content becoming a
trusted internal belief simply by being stored — is the failure that makes the first two
defences irrelevant a week later.

### 3.2 Provenance is mandatory

Every memory carries `Provenance` ([03](03-module-contracts.md) §4): tier, source
reference, derivation chain, and the model that produced it. This is what makes
*"why do you think that?"* answerable down to a URL or a sentence the user said, and it is
what makes a privacy purge possible ([21](21-security-privacy-ethics.md) §7).

---

## 4. Local documents

The best knowledge source in the system: private, curated, high signal, no network.

```mermaid
graph LR
    A["hedwig docs add ~/notes"] --> B["walk + filter<br/><small>allowed extensions, size cap, ignore rules</small>"]
    B --> C["extract text<br/><small>md, txt, pdf, code, html</small>"]
    C --> D["chunk<br/><small>structure-aware: headings, then ~500 tokens, overlap 60</small>"]
    D --> E["embed + FTS index"]
    E --> F["document + document_chunk rows"]
    F --> G["retrievable at CURATED trust"]
```

| Concern | Approach |
|---|---|
| Chunking | Structure-first (heading boundaries, code blocks intact), then token-window with overlap. `heading_path` is stored and included in the rendered chunk, which measurably improves usefulness for the model. |
| Change detection | `content_hash` per file; a watcher (opt-in) or manual `reindex` |
| Removal | `forget_path` tombstones chunks and deletes vectors; `document.removed_at` set |
| Secrets | An ingest-time scanner skips files matching secret patterns (`.env`, keys, tokens) and reports them rather than indexing them |
| Size | Per-file cap 5 MB, per-ingest cap configurable; PDFs text-extracted only |

Documents are **not** promoted into beliefs automatically. They are retrieved as documents,
with attribution. Turning a user's notes into HEDWIG's asserted beliefs would erase the
distinction between "you wrote this" and "I think this", and that distinction is worth
keeping.

---

## 5. The tool layer

Tools are curated and in-tree ([01](01-vision-and-scope.md) §4 — no plugin marketplace).
Each declares a full `ToolSpec` ([03](03-module-contracts.md) §5.8).

### 5.1 Initial tool set

| Tool | Side effects | Approval | Output trust | Phase |
|---|---|---|---|---|
| `search_memory` | none | no | `SELF` | 1 |
| `search_documents` | none | no | `CURATED` | 2 |
| `read_file` (within allowed roots) | none | no | `CURATED` | 2 |
| `get_datetime` | none | no | `TOOL` | 1 |
| `calculate` | none | no | `TOOL` | 2 |
| `list_goals` / `update_goal` | local_write | no | `SELF` | 3 |
| `pin_memory` / `forget_memory` | local_write | **yes** | `SELF` | 4 |
| `fetch_url` (allowlisted) | network | **yes** in conversation; auto in curiosity within budget | `UNTRUSTED` | 7 |
| `web_search` | network | **yes** | `UNTRUSTED` | 7 |
| `write_file` | external_write | **yes**, always | `TOOL` | later |

`forget_memory` requires approval even though it is a local write. Deleting a memory on a
model's judgement is exactly the operation that should never be silent.

### 5.2 Execution

```mermaid
sequenceDiagram
    participant B as brain
    participant R as ToolRegistry
    participant G as governor
    participant T as tool impl
    participant DB as tool_call

    B->>R: execute(name, args, correlation_id)
    R->>DB: insert status=requested
    R->>R: validate args (JSON Schema)
    alt invalid
        R-->>B: ToolArgumentInvalid (one retry allowed)
    end
    alt requires_approval
        R-->>B: raise ToolApprovalRequired
        Note over B: graph interrupts, user decides
    end
    R->>G: admit(resource=side_effect_class)
    R->>T: run(args) with timeout
    T-->>R: result
    R->>DB: status=ok, duration, digest
    R-->>B: ToolResult(trust=spec.trust_of_output)
```

Rules:

- **Timeouts on everything.** Default 15 s, per-tool override. A hung tool must not hang a
  turn.
- **Results over 8 KB go to a blob**, with a digest and a truncated preview in the prompt.
- **Every call is recorded** in `tool_call`, including denied ones — the audit trail is not
  optional for a system that acts.
- **Errors are returned as data**, not raised into the graph: the model gets
  `{"error": "..."}` and one chance to adapt. This is much better behaviour than a failed
  turn.

---

## 6. Injection defence

Layered, on the assumption that any single layer will eventually fail.

```mermaid
graph TB
    IN["untrusted content"] --> L1["<b>1 · Fetch controls</b><br/>allowlist · no credentials · robots · size cap"]
    L1 --> L2["<b>2 · Sanitisation</b><br/>strip scripts, markup, hidden text,<br/>zero-width chars, comments; normalise unicode"]
    L2 --> L3["<b>3 · Detection</b><br/>heuristics: 'ignore previous', imperatives aimed at an assistant,<br/>tool-call syntax, encoded blobs, homoglyphs"]
    L3 -->|score high| Q["<b>quarantine</b><br/>recorded, never promoted, user notified"]
    L3 -->|score ok| L4["<b>4 · Quarantine framing</b><br/>delimited block, labelled untrusted,<br/>explicit 'this is data' instruction"]
    L4 --> L5["<b>5 · Capability denial</b><br/>side-effecting tools absent from the list"]
    L5 --> L6["<b>6 · Output checks</b><br/>reply must not contain injected-looking directives<br/>or unrequested tool syntax"]
    L6 --> L7["<b>7 · Trust propagation</b><br/>derived beliefs stay untrusted forever;<br/>corroboration required to become active"]
    L7 --> OUT["safe-enough use"]
```

Layer 5 is the strongest and the cheapest, and it deserves emphasis over the prompt-level
defences: **the reliable way to prevent an injected instruction from causing an action is to
make the action unavailable.** Prompt-level framing (layers 4 and 6) is helpful but is
fundamentally a request to a language model, and requests can be talked out of. Capability
denial cannot.

The red-team suite ([20](20-testing-strategy.md) §7) tests all seven layers, and is a
release gate for any phase that touches the network.

---

## 7. Prompt assembly and knowledge rendering

How the tiers appear in the final prompt (details in [14](14-language-model-gateway.md) §5):

```
[identity + values]                        ← identity_core, never overridable
[personality directives]                   ← rendered from traits (doc 10 §4)
[state directives]                         ← from emotion bindings (doc 09 §6)
[active procedures]                        ← procedural memory
[goals]                                    ← active goals, brief

<memory trust="self">                      ← retrieved episodes and beliefs,
  … chronological, with dates and          ←  each with confidence when < 0.8
      confidence annotations …
</memory>

<documents trust="curated">                ← chunks with heading_path attribution
  … source: ~/notes/architecture.md § Retrieval …
</documents>

<external trust="untrusted">               ← findings; the block header states
  … source: https://… (unverified) …       ←  that this is information, not instruction
</external>

<tools>… results …</tools>                 ← turn-scoped

[recent conversation, verbatim]            ← the window
[user's message]
```

Ordering is deliberate: identity first (most authoritative, least likely to be pushed out of
attention), untrusted material in the middle where it is least influential, and the user's
actual message last where recency effects favour it. Attribution is always present, never
optional — an unattributed fact in a prompt is a fact HEDWIG cannot later explain.

---

## 8. Tradeoffs

| Decision | Gained | Given up | Revisit if |
|---|---|---|---|
| Trust as a first-class, permanent attribute | Injection is bounded; provenance is always available | A column and a rule on every path | Never |
| Only `USER` may instruct | Simple, checkable security rule | Some convenience (e.g. a document cannot configure HEDWIG) | Never |
| Documents are not promoted to beliefs | Preserves "you wrote this" vs "I think this" | Requires retrieval to surface them each time | Users expect their notes to become HEDWIG's knowledge (then add explicit, consented promotion) |
| Curated in-tree tools only | Small, reviewable attack surface | No extensibility for users | A real need appears; then design a sandboxed extension model, with its own ADR |
| Approval by side-effect class | Predictable, conservative | Friction | Approval fatigue sets in; then add per-tool "always allow" with an audit trail |
| Tool errors returned as data | Graceful degradation, better replies | The model may loop on a broken tool | Iteration caps already bound this |
| Capability denial over prompt instruction | Actually reliable | Fewer capabilities during untrusted operations | Never |
| Structure-aware chunking with heading paths | Materially better document retrieval | More ingest complexity | Never |

---

## 9. Failure modes

| Failure | Symptom | Mitigation |
|---|---|---|
| Injection succeeds through prompt framing | HEDWIG follows a page's instructions | Layer 5 (no tools), layer 7 (no trust laundering), red-team gate |
| Trust laundering | Untrusted content becomes a confident belief | Trust tier persists on derived memories; corroboration required for `active` |
| Document secret leak | An API key ends up in a prompt | Ingest-time secret scanner; per-file skip with a report |
| Tool hangs | Turn stalls | Timeouts, governor, iteration caps |
| Tool argument hallucination | Repeated failures | Schema validation, error-as-data, one retry, then compose without it |
| Approval fatigue | User approves everything reflexively | Few tools require approval; approval prompts state exactly what will happen; batch approvals are not offered |
| Stale documents | Answers from an old file version | `content_hash` detection, optional watcher, `ingested_at` shown in attribution |
| Allowlist creep | Everything is allowed | Additions are per-domain, user-approved, and listed in the inspector |
| Over-retrieval of untrusted material | Replies full of hedged web content | ×0.6 weight, kind quotas in packing, `untrusted` share metric per turn |

---

## 10. Testing

- **Trust propagation test** — an untrusted finding becomes a belief; assert the tier
  survives merges, summarisation, and re-embedding.
- **Injection red-team suite** — adversarial corpus, all seven layers, release gate.
- **Tool contract tests** — every tool: schema validity, timeout behaviour, error-as-data,
  approval enforcement, audit row written.
- **Capability denial test** — during an untrusted-content operation, assert the tool list
  contains no side-effecting tools.
- **Prompt assembly snapshot tests** — golden prompts for canonical situations; catches
  accidental reordering or a missing trust label.
- **Ingest tests** — chunk boundaries, heading paths, secret skipping, hash-based change
  detection, `forget_path` completeness (row, FTS, vector, blob).
- **Attribution test** — every fact in a rendered prompt block is traceable to a source ref.

---

## 11. Future improvements

| Improvement | Trigger |
|---|---|
| Sandboxed user-defined tools (WASM or subprocess with a capability manifest) | Genuine user demand; needs its own ADR and threat model |
| Source reputation learned from corroboration outcomes | Enough findings to compute reliability per domain |
| Consented promotion of document content into beliefs | Users ask for it |
| Multi-modal documents (images, diagrams) | Vision model in the stack |
| Incremental document watching with debounce | Users work in a directory HEDWIG follows |
| A "why do you trust this?" UI affordance on every claim | Phase 5 inspector; cheap and high-value |
| Structured tool output types (not just text) | Tools start returning tables the UI should render |

---

## 12. Implementation record — the guard and the tool layer (COG-01, COG-02)

Until this, the planner already sent "what time is it?", arithmetic and "do you remember…"
to tools, and the runner was a stub: the model was shown the literal string
`[stub] get_datetime()` as the answer. Real tools are a correctness fix, not a feature.

```mermaid
graph LR
    P["RulePlanner<br/><small>which tool, which args</small>"] --> R["tools.ToolRegistry<br/><small>ToolRunner port</small>"]
    R -->|validate: JSON Schema| R
    R -->|audit| DB[("tool_call")]
    R --> T1["get_datetime<br/><small>clock + timezone</small>"]
    R --> T2["calculate<br/><small>AST arithmetic, no eval</small>"]
    R --> T3["search_memory<br/><small>HybridRetrieval</small>"]
    R -.->|KNW-02| T4["web_search · read_page"]
```

* **`ToolRegistry`** implements the brain's `ToolRunner`. Each tool declares a name, a
  description, a JSON Schema for its arguments, its side effects and the trust of its output.
  Arguments are validated before the tool runs. Every call writes a `tool_call` row (migration
  0012: name, arguments, status, duration, correlation id) whether it succeeds or not. A tool
  that raises or overruns its timeout becomes a failed `ToolOutcome`, never an exception
  (docs/07 §8).
* **`calculate` never calls `eval`.** It walks the Python AST and admits numbers, `+ - * / // %
  **`, unary signs and parentheses. Exponents are bounded, so `9**9**9` is refused rather than
  hanging the process.
* **`get_datetime` reads the injected clock** in `runtime.timezone`, so it is correct in a
  test at a fake time and in a user's local zone.
* **The guard (COG-01)** replaces `PermissiveGuard`. The user's messages are always allowed and
  always `USER` trust: ADR-0011 makes the user the one instruction source, and a companion that
  refuses its own user on a heuristic is broken, not safe. What it adds is observation. Each
  input is scored by `knowledge.injection`, and flags (pasted role markers, override phrasing,
  zero-width characters) are recorded on the verdict, which emotion already appraises
  (docs/07 §14.3). **It refuses nothing today**, and says so here rather than claiming more.
* **`knowledge.injection`** is the deterministic layer-3 detector of §6, shared by the guard
  and by everything that reads the network (KNW-02). It is pure functions with no model, so it
  cannot be talked out of a verdict.

| Tradeoff | Gained | Given up | Revisit if |
|---|---|---|---|
| Rule guard that observes, never refuses | No false refusals of the user | No protection against the user's own pasted injection | Pasted content becomes a channel (documents, KNW-01): score it as `CURATED`, not `USER` |
| Approval not implemented | Read-only tools need none | Side-effecting tools must wait | COG-03 with the WebSocket |

---

## 13. Implementation record — web research (KNW-02, KNW-03, COG-04)

**Decided with the user (2026-11-20): DuckDuckGo, opt-in.** No API key, no account. Off by
default (`knowledge.web_enabled = false`). When on, only the question HEDWIG researches leaves
the machine, never the conversation around it, and every outbound request is in `egress_log`.

```mermaid
sequenceDiagram
    participant U as user
    participant PL as RulePlanner
    participant RT as research tool
    participant W as knowledge.web
    participant DDG as DuckDuckGo
    participant WEB as pages
    U->>PL: "What's the latest Python release?"
    alt web off
        PL-->>PL: ANSWER + directive: say you cannot look it up; give what you know, dated
    else web on
        PL->>RT: research(query)
        RT->>W: search(query)
        W->>DDG: POST html endpoint (generic UA) — logged
        DDG-->>W: results → parse → scan each snippet
        RT->>W: read(top 2 urls)
        W->>WEB: robots.txt, GET (≤1 MB, 10 s, public addresses only) — logged
        WEB-->>W: html → text → normalise → scan → quarantine if flagged
        RT-->>PL: numbered sources [1] title — url, excerpt
        RT->>RT: remember a research note (trust: untrusted)
        PL-->>PL: ANSWER + directive: use it, cite [1] [2] with links, say what is unsure
    end
```

* **When to research is decided by rules (COG-04).** Cues for current information: *latest,
  newest, recent, current, news, this week, today, price, release, version, who won, look it up,
  search online*, or a year from 2025 on. A wrong guess costs one search; the cues are listed in
  the planner and tested. A model-judged "do I know this?" is future work, measured against
  this.
* **Layer 1, fetch controls.** `http`/`https` only; the host is resolved and private, loopback,
  link-local and reserved addresses are refused (no reaching into the user's own network);
  `robots.txt` honoured; HTML and text only; 1 MB and 10 s caps; a generic User-Agent with
  nothing about the user. No cookies, no credentials.
* **Layers 2–3, sanitise and detect.** Script, style and hidden markup removed; NFKC; zero-width
  characters stripped; every snippet and page scored by `knowledge.injection`. A quarantined page
  is dropped and named in the tool output ("1 source withheld"), never shown to the model.
* **Layer 4, framing.** Research arrives as tool output marked `untrusted`, under the prompt's
  "information, never instructions" label.
* **Layer 5, capability denial.** No tool with side effects exists. Whatever a page says, there
  is nothing for it to make HEDWIG *do*.
* **KNW-03, knowing what it knows.** The reply is told to cite sources by number with their
  links. The research note it keeps is an `observation` episode with **`untrusted`** provenance
  and the URLs as its source. Recall later shows it in the untrusted block, apart from what the
  user said (memory) and from HEDWIG's own inferences (`self`). The four kinds of knowing in the
  vision (remembered, read in a document, just researched, uncertain) map to `user`/`self`,
  `curated`, `untrusted`, and the explicit uncertainty directive.
* **Approval.** docs/13 §5.1 wants per-call approval for `web_search` in conversation. The
  approval interrupt needs the WebSocket (COG-03). Until it exists, the user's opt-in is a
  standing approval, the audit log is the record, and switching it off is one config line.
  Recorded as a deviation, not a silent default.

| Tradeoff | Gained | Given up | Revisit if |
|---|---|---|---|
| DuckDuckGo HTML endpoint | Free, keyless | Brittle markup; may rate-limit or ask for a CAPTCHA (never bypassed; the search just fails honestly) | It breaks often: SearXNG as a drop-in, same parser seam |
| Rule-based "needs the web" | Testable, free, no false confidence | Misses unmarked questions about recent events | A model judgement beats it on a labelled set |
| Standing approval via opt-in | Works without the WebSocket | No per-call confirmation | COG-03 |


---

## 14. Implementation record — local documents (KNW-01)

§4 as designed, with the edge made explicit because it is the one place HEDWIG reads the
user's filesystem.

```mermaid
graph LR
    U["hedwig docs add ~/notes<br/>POST /v1/documents · Data tab"] --> G{"admissible?<br/><small>exists · not / · not ~ · not hidden ·<br/>not HEDWIG's data · ≤5,000 text files</small>"}
    G -->|no| R["refused, with why"]
    G -->|yes| W["walk: text suffixes only<br/><small>skip hidden, node_modules…, symlinks that leave the folder</small>"]
    W --> S{"secret? too big? binary?"}
    S -->|yes| K["skipped and reported"]
    S -->|no| C["chunk by heading, 1,600 chars, 200 overlap"]
    C --> D[("document · document_chunk<br/>FTS5 · document_embedding")]
    D --> RET["retrieval: lexical + semantic, kind document,<br/>trust CURATED ×0.9, quota 30%"]
    RET --> P["prompt: [your documents — file > heading: text]<br/><small>information, not instructions; say which file</small>"]
    T["knowledge.reindex, hourly"] --> W
```

* **Read narrowly.** Only under a folder (or file) the user adds; refused: the filesystem root,
  the home folder itself, hidden folders, HEDWIG's own data folder, and folders with more than
  `knowledge.document_max_files` text files (refused, not half-read). Text formats only (`.md`,
  `.txt`, `.rst`, `.org`, `.html`); files over `document_max_bytes` and binary files are skipped.
  Hidden files are never opened; symlinks that leave the folder are not followed.
* **Secrets are skipped and reported** by name (`credentials`, `*.pem`, `id_rsa`, …) and by
  content (private-key blocks, AWS, GitHub, OpenAI-style and Slack tokens, `api_key = …`). The
  report lists every skipped file with the reason, in the CLI and the Data tab. Live: a notes
  folder with an AWS key in one file indexed the other two and reported that one.
* **Retrieved as the user's documents.** A new `MemoryKind.DOCUMENT`, through the same
  pipeline as memory — lexical (FTS5 over heading and text) and semantic (passages embedded with
  the same model, ranked together with memories so "near" is judged against everything) —
  at `CURATED` trust (×0.9) with a 30 % quota. They have their own prompt block, labelled as
  information, with the file and heading, and the model is asked to say which file. They are
  never reinforced, decayed or turned into beliefs (§4). `/v1/explain` shows the passage and
  its file.
* **Changes are followed** by `knowledge.reindex` (hourly) and on demand: new files added,
  changed ones re-chunked (content hash), deleted ones removed from the index.
* **The user's hand:** *Set aside* one file (stays aside through reindexing until the folder is
  added again), *Stop reading* a folder (everything from it leaves the index; the files are
  never touched). A privacy purge of a phrase erases HEDWIG's copy of every passage of a file
  containing it and sets the file aside — half a note with the erased part missing would read
  as something the user never wrote.
* **Found while testing:** adding a folder again did not bring back a file set aside, because
  its unchanged hash made it "unchanged" with no passages. Re-adding now forces a fresh read.
* **Live** (gemma4:e2b, nomic-embed-text): "When should I plant garlic?" and "How often do I feed
  my starter?" were answered from the notes, naming the file. "What do I put in the ground in
  autumn?" — no shared words — recalled the right note by meaning; the 2B model then did not use
  it, which is the model's limit, not retrieval's.

| Tradeoff | Gained | Given up | Revisit if |
|---|---|---|---|
| Text formats only | No new parser dependency, nothing opaque ingested | PDFs and Word files | Users keep notes in PDF: add `pypdf` (a dependency, asked first) |
| Refuse rather than half-read big folders | The user knows exactly what HEDWIG reads | A large vault needs a narrower folder | Per-subfolder selection in the UI |
| Hourly reindex, not a file watcher | No background OS hooks | Up to an hour stale | Watchers become cheap and portable |
| A whole file set aside on an erased phrase | No half-notes | The rest of that file leaves recall | Users want passage-level exclusions |

**Future improvements.** PDF and `.docx` text extraction; a file watcher; per-document trust
("this folder is drafts"); citations rendered as links in the app; documents in the export's
readable part.
