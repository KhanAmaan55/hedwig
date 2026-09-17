# ADR-0017 — The service registry manages lifecycle; it is not a service locator

**Status:** Accepted
**Date:** 2026-08-09
**Affects:** docs/03 §6, docs/17 §3-4, docs/25

## Context

Milestone 2 calls for a "Service Registry". docs/03 §6 is explicit in the other direction:
wiring is *"a plain function, not a DI framework… no decorators, no service locator, no
magic."* A registry that components call at runtime to fetch collaborators is exactly the
service-locator pattern that rule rejects — it hides dependencies, makes them
untyped, and turns a readable constructor into a runtime lookup that fails late.

But there is a real problem the composition root does not solve. Construction is static and
belongs in one readable function. *Lifecycle* is dynamic: services must start in dependency
order, stop in reverse, unwind cleanly when one fails to start, and report health. Putting
that in `build_container` turns a list of constructors into a state machine.

## Decision

Split the two concerns, and constrain what the registry is allowed to be used for.

* **`wiring.py` constructs.** Every service receives its dependencies through its
  constructor, typed against ports. Nothing looks anything up to be built.
* **The registry manages what happens next**: `start_all` in topological order,
  `stop_all` in reverse, transactional startup (a failing critical service stops everything
  already started and re-raises), and aggregated `health()`.
* **`get()` is for diagnostics and plugins only.** Application code does not resolve
  collaborators by name at runtime. A plugin uses it because it is loaded after the graph is
  built and cannot be handed dependencies at construction time — the one case where a
  late lookup is unavoidable.
* Registration is refused once started, so the set of services is fixed at startup and the
  registry cannot become a mutable global.

## Consequences

Startup and shutdown are ordered, observable, and testable, without the composition root
growing lifecycle logic. Health has one place to come from, which is what let `/v1/health`
report every service without knowing what any of them are.

The cost is one more concept, and a `get()` method that could be abused. The abuse is
bounded by the layering contracts in `.importlinter` (nothing above `core` may import a
concrete service) and by the rule stated here, which reviewers can point at.

## Alternatives considered

* **A DI container with autowiring.** Rejected for the reasons in docs/03 §6: it replaces a
  readable function with a framework whose failure mode is a runtime resolution error.
* **Lifecycle methods on the container itself.** `Container.start()` looping over its own
  fields. Rejected: no dependency ordering, no failure unwinding, and the container would
  need to know which fields are services.
* **No registry; start things in `wiring.py`.** Works for five services, fails for twenty,
  and gives no place for health to live.

## Revisit if

Application code starts reaching for `registry.get()` outside diagnostics and plugins. That
is the signal the split has eroded, and the fix is to pass the dependency in, not to relax
the rule.
