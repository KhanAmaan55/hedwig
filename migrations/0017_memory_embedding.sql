-- 0017 — Embeddings for semantic recall (docs/06 §17.2, MEM-04). Owner: `memory`. New table only.
--
-- One vector per memory. `model` is the model-identity guard (docs/05 §5.3): only vectors from
-- the configured model are searched, so changing the model never mixes two vector spaces; the
-- `memory.embed` task re-embeds the rest. `text_hash` says when the text changed under a vector.
-- A plain table, not a `vec0` virtual table: search is `vec_distance_cosine` over it, and purge
-- and export see an ordinary table (ADR-0024).

CREATE TABLE memory_embedding (
    memory_id   TEXT PRIMARY KEY,
    memory_kind TEXT NOT NULL CHECK (memory_kind IN ('episode','belief')),
    model       TEXT NOT NULL,
    dims        INTEGER NOT NULL,
    vector      BLOB NOT NULL,           -- float32, little-endian
    text_hash   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_memory_embedding_model ON memory_embedding (model);
