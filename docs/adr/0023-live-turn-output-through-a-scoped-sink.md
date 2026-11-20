# ADR-0023 — Live turn output travels through a context-scoped sink, not the event bus

**Status:** Accepted
**Date:** 2026-11-20
**Affects:** docs/16 §5, §13, docs/04, docs/07, `core/live.py`, `llm/responder.py`, `brain/graph.py`, `api/routes/stream.py`

## Context

The WebSocket (FND-05) has to deliver a reply token by token, and the turn's stages
("recalling…", "composing…") as they happen. Tokens are produced deep inside the turn, in the
responder, which a graph node calls. The stream belongs to one connection.

Three things constrain the path from the responder to that connection:

* **The event bus is a durable outbox** (docs/04 §3). Every event is validated and written to
  SQLite before dispatch. A reply is a few hundred tokens; writing each one to disk to deliver it
  to a socket is waste, and replaying them after a restart would be wrong. ADR-0009 made the same
  call for expression frames.
* **Ports and graph state must not change for a transport.** `TurnState` is checkpointed and
  must stay JSON-serialisable (docs/07 §4 rule 3), so it cannot carry a callback. The
  `Responder` port should not grow a "stream to whom" parameter that a CLI turn and a test never
  use.
* **Correlation already works this way.** `core.context` binds the correlation id in a
  `contextvars` scope that follows `await` and spawned tasks, and every log line and record
  picks it up without an argument being threaded through.

## Decision

1. **`core.live` holds a context-scoped sink**: `live_scope(sink)` binds an async callable for a
   block; `await emit_live(kind, payload)` calls it if one is bound and does nothing otherwise.
2. **The WebSocket binds it around `Brain.run_turn`.** The responder, when a sink is bound,
   streams from the gateway and emits each piece as `token`. The graph emits `stage` as the
   recall, deliberate, act and compose nodes begin. Nothing else changes: REST, the CLI and tests
   bind no sink, and the responder generates in one call as before.
3. **Live output is ephemeral.** It is not persisted, not on the bus, and not replayed. The
   authoritative reply is the one `message.complete` carries, built from the final turn state.
   That is the same text the REST path returns and the conversation log stores.
4. **A failing sink never fails a turn.** A closed socket makes `emit_live` a no-op for the rest
   of the turn, which completes and persists as if nobody were watching (docs/16 §9: "client
   disconnects mid-turn → turn completes and persists").

## Consequences

* Streaming adds one module and a few lines in two places; no port, no graph state, no event
  type changes.
* Whether a turn streams is decided by who called it, not by configuration.
* Ambient context is less visible than an argument. Mitigated by keeping the surface to two
  functions, documenting them here, and testing that a sink bound around `run_turn` really
  receives tokens from inside the graph.
* A reply that fails mid-stream has already shown partial text; the client replaces it with
  `message.complete`'s honest failure reply.

## Alternatives considered

* **Bus events for tokens.** Rejected for the reasons in Context. A durable outbox for ephemeral
  data, and a replay hazard.
* **A streaming method on the `Responder` port, with a callback parameter.** Rejected: every
  responder, stub and fake would have to accept it, and the graph would have to pass a non-
  serialisable callback through checkpointed state to reach the node.
* **LangGraph's own streaming (`astream_events`).** It would surface the model's tokens only if
  the model were a LangChain runnable, which ours is deliberately not (ADR-0004: nodes are
  adapters), and it would tie the wire format to LangGraph's event schema.

## Revisit if

* A second consumer of live output appears that is not a connection (e.g. a TTS that speaks
  sentence by sentence as they stream). Then the sink becomes a small fan-out, still in-process.
* HEDWIG ever runs turns in a separate process from the API (ruled out by ADR-0001).
