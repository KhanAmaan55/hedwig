# ADR-0024 — Purge is one sweep over a classified schema, verified against the file

**Status:** Accepted
**Date:** 2026-11-20
**Affects:** docs/21 §7, docs/05 §3 rule 5, docs/08 §3.1, `src/hedwig/privacy/`, migration 0016

## Context

Purge is the one operation in HEDWIG that must delete for real (docs/21 §7.3). Everything else
supersedes or tombstones. A purge that misses a copy is a broken promise, and content is copied
widely: a user message lives in `message`, in the `conversation.message.received` event payload,
in the turn's episode, in the beliefs reflection extracted from it, in the day summary, in the
tombstone archive if it was forgotten, in FTS index segments, in freed database pages, in the WAL
file, and in every pre-migration backup.

docs/08 §3.1 says writes go through the module that owns a table. Followed literally, purge would
be a `purge(ids)` hook on every owner, invoked by event. That has two problems:

* **Silent omission.** Exhaustiveness would depend on every present and future module
  remembering to implement the hook and to cover every column. Nothing would fail when one did
  not; the content would just survive.
* **No single place can verify.** Only something that sees the whole file can say "zero
  residue".

## Decision

1. **One module, `hedwig.privacy`, performs purge as a single sweep**, directly on the tables.
   This is the documented exception to "writes go through the owner" (docs/05 already allows
   `DELETE` only "in the explicit privacy purge"). It also builds exports, which read every table.
2. **Every table is classified** in `privacy/tables.py`: `CONTENT` (holds user content: deleted by
   target, swept by id), `RECORD` (logs and edges that quote or point at content: rows referring
   to a target are deleted), `INDEX` (FTS shadow tables, rebuilt), `SYSTEM` (no user content:
   kept). **An architecture test fails when a migration adds a table nobody classified**, which
   turns "a new module forgot purge" from a silent leak into a red build.
3. **Targets are resolved before anything is deleted**, including every memory derived from a
   target through `memory_derivation`, so a paraphrase does not survive its source. The user sees
   the counts and examples, and confirms with a code that is a hash of the exact target set; if
   anything changed in between, the purge refuses.
4. **The purge verifies itself against the database file**: `secure_delete` while deleting, FTS
   `optimize`, a full `VACUUM` (zeroing deleted cells misses the slack of rebuilt pages, which
   the completeness test caught), WAL checkpoint, then a byte scan of the file for fragments of
   every purged text. A fragment that is found but belongs to a surviving row is reported ("also
   said elsewhere"); one that belongs to nothing is a failure.
5. **Backups made before the purge are deleted by it**, because they contain the content. The
   preview says so first.
6. **Erasing everything is offline**: `hedwig purge --everything` refuses while HEDWIG is running
   and deletes the data directory's database, blobs, backups, logs and token. A running process
   holds state in memory that a table sweep cannot reach; stopping it first is simpler and
   complete.

## Consequences

* Purge is one readable module and one test, not twelve hooks.
* `privacy` knows the schema. A table's meaning changing is now two edits (the owner and the
  classification); the architecture test makes the second impossible to forget for new tables,
  not for changed ones — changed columns are caught by the completeness test, which seeds content
  through the real flows and scans the file.
* Deleting backups trades the migration safety net for honesty about deletion. The next
  migration makes a new backup.
* Running services cache little, and none of it is content, so a scoped purge needs no restart.
  Purge refuses while a turn is running, and drains the event bus first, so no in-flight handler
  can re-create what was just deleted.

## Alternatives considered

* **A purge hook per owning module, fanned out by event.** Rejected: see Context. Kept as the
  future shape if modules ever live in separate stores, at which point each store needs its own
  verifier anyway.
* **Encrypt each memory with its own key and delete the key ("crypto-shredding").** Strong, but
  every read path, every index and FTS would have to change, and FTS over ciphertext does not
  work. Revisit with SQLCipher (docs/21 §8).
* **Purge as tombstone plus nightly hard-delete.** Rejected: the user asked for deletion now, and
  a window in which the content still exists is exactly what purge is for.
