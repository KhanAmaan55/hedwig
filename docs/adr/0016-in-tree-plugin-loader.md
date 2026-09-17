# ADR-0016 — In-tree plugin loader, not an extension surface

**Status:** Accepted
**Date:** 2026-08-09
**Affects:** docs/01 §4, docs/13 §5, docs/25

## Context

Milestone 2 calls for a "Plugin Loader". That word collides with an explicit non-goal:
docs/01 §4 says HEDWIG is *"not a general assistant platform. No plugin marketplace, no
arbitrary third-party extension surface. Tools are curated and in-tree."* docs/13 §8 adds
that any real extension model needs its own threat model.

Both things can be true. The value in a loader is not third-party extensibility; it is that
optional first-party subsystems — a speech adapter, a document watcher, a future avatar
emitter — can be switched off by configuration instead of by editing the composition root,
and that a failure in one does not take down startup.

The risk is that a loader *looks* like an extension point, and a later contributor treats it
as one.

## Decision

Build an **in-tree, allowlist-only** loader, and say what it is not, in the module docstring
and here:

* Plugins are listed explicitly in `plugins.enabled` as importable module paths. Nothing is
  discovered by scanning a directory, downloaded, or installed at runtime.
* A plugin is ordinary Python in this repository, reviewed like any other code.
* It runs with **full process privileges**. There is no sandbox and no security boundary.
* The loader provides ordering (topological, over declared dependencies), failure isolation
  (an optional plugin that fails is recorded and skipped; a `required` one aborts startup
  and tears down what already loaded), and a lifecycle (`setup` / `teardown`).
* Plugins receive a narrow `PluginContext`: config, the service registry, their own settings
  block, and a logger factory. Anything else is asked for through the registry by name, so
  the dependency is visible rather than reached for.
* The allowlist is empty by default.

## Consequences

Optional subsystems become configuration rather than edits to `wiring.py`, and one broken
optional module degrades the system instead of stopping it.

We accept that this is *not* a security mechanism. That is stated in the port, in the
implementation, and in the tests, because a boundary that does not hold is worse than an
absent one — people rely on the former.

We also accept a small amount of duplication with the service registry: both do topological
ordering. They order different things (plugins vs. services) at different times, and merging
them would couple loading to lifecycle.

## Alternatives considered

* **No loader; wire everything in `wiring.py`.** Simplest, and it was the Milestone-1
  position. Rejected because every optional subsystem then needs a conditional in the
  composition root, and a failure in any of them aborts startup.
* **Entry-point discovery (`importlib.metadata`).** The standard Python plugin mechanism.
  Rejected outright: it makes any installed package a plugin, which is precisely the
  third-party extension surface docs/01 §4 forbids.
* **A sandboxed extension model** (subprocess or WASM with a capability manifest). This is
  the honest way to support third-party code, and it is a project of its own with a threat
  model attached. Deferred; docs/13 §11 already lists it with its trigger.

## Revisit if

Genuine demand for third-party extensions appears. That is a new ADR with a threat model,
not an expansion of this one.
