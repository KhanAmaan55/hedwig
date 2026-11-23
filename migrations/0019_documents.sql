-- 0019 — Local documents (docs/13 §4, §14; KNW-01). Owner: `knowledge`; embeddings: `memory`.
-- New tables only.
--
-- `document_root`: folders (or files) the user pointed HEDWIG at. Only these are ever read.
-- `document`: one file. `removed_at` excludes it — the user removed it, or erased what it said —
--   and reindexing leaves an excluded file alone until the user adds it again.
-- `document_chunk`: passages, retrieved at CURATED trust with `heading_path` for attribution.
-- `document_embedding`: one vector per chunk, model-guarded like `memory_embedding` (0017).

CREATE TABLE document_root (
    path        TEXT PRIMARY KEY,
    added_at    TEXT NOT NULL
);

CREATE TABLE document (
    id            TEXT PRIMARY KEY,
    root          TEXT NOT NULL REFERENCES document_root(path),
    path          TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    mime          TEXT,
    bytes         INTEGER,
    modified_at   TEXT NOT NULL,
    trust_tier    TEXT NOT NULL DEFAULT 'curated',
    ingested_at   TEXT NOT NULL,
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    removed_at    TEXT
);

CREATE TABLE document_chunk (
    id            TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL REFERENCES document(id),
    seq           INTEGER NOT NULL,
    text          TEXT NOT NULL,
    heading_path  TEXT,
    token_count   INTEGER,
    created_at    TEXT NOT NULL,
    UNIQUE (document_id, seq)
);

CREATE VIRTUAL TABLE document_chunk_fts USING fts5(
    heading_path, text,
    content='document_chunk', content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER document_chunk_fts_insert AFTER INSERT ON document_chunk BEGIN
    INSERT INTO document_chunk_fts (rowid, heading_path, text)
    VALUES (new.rowid, coalesce(new.heading_path, ''), new.text);
END;

CREATE TRIGGER document_chunk_fts_delete AFTER DELETE ON document_chunk BEGIN
    INSERT INTO document_chunk_fts (document_chunk_fts, rowid, heading_path, text)
    VALUES ('delete', old.rowid, coalesce(old.heading_path, ''), old.text);
END;

CREATE TABLE document_embedding (
    chunk_id    TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    dims        INTEGER NOT NULL,
    vector      BLOB NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_document_live ON document (removed_at);
CREATE INDEX idx_document_embedding_model ON document_embedding (model);
