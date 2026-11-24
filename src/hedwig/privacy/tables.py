"""What every table holds, from privacy's point of view (ADR-0024).

Purge and export are only as complete as this map. `tests/architecture` fails when a migration
adds a table that is not here, so a new module cannot silently fall outside purge.
"""

from __future__ import annotations

from enum import StrEnum


class Kind(StrEnum):
    CONTENT = "content"
    """Holds what the user said or what HEDWIG concluded about them. Purge targets rows here."""
    RECORD = "record"
    """Logs and edges that quote or point at content. A row mentioning a target id goes with it."""
    INDEX = "index"
    """FTS shadow tables, maintained by triggers and rebuilt after a purge."""
    SYSTEM = "system"
    """No user content: numbers, schema, scheduler state. Kept."""


TABLES: dict[str, Kind] = {
    # what the user said, and what HEDWIG made of it
    "session": Kind.CONTENT,
    "message": Kind.CONTENT,
    "episode": Kind.CONTENT,
    "belief": Kind.CONTENT,
    "goal": Kind.CONTENT,
    "gap": Kind.CONTENT,
    "finding": Kind.CONTENT,
    "entity": Kind.CONTENT,
    "document": Kind.CONTENT,
    "document_chunk": Kind.CONTENT,
    # where copies and pointers end up
    "turn": Kind.RECORD,
    "event": Kind.RECORD,
    "event_dead_letter": Kind.RECORD,
    "processed_event": Kind.RECORD,
    "working_set_log": Kind.RECORD,
    "memory_access": Kind.RECORD,
    "memory_derivation": Kind.RECORD,
    "memory_entity_link": Kind.RECORD,
    "memory_embedding": Kind.RECORD,
    "document_embedding": Kind.RECORD,
    "document_root": Kind.RECORD,
    "tombstone": Kind.RECORD,
    "tool_call": Kind.RECORD,
    "llm_call": Kind.RECORD,
    "appraisal": Kind.RECORD,
    "emotion_history": Kind.RECORD,
    "belief_judgement": Kind.RECORD,
    "egress_log": Kind.RECORD,
    "drift_proposal": Kind.RECORD,
    "personality_evidence": Kind.RECORD,
    "goal_note": Kind.RECORD,
    "identity_report": Kind.RECORD,
    "initiative_offer": Kind.RECORD,
    "correction_proposal": Kind.RECORD,
    "span": Kind.RECORD,
    "relationship_tally": Kind.RECORD,
    "state_document": Kind.RECORD,
    "state_history": Kind.RECORD,
    "state_snapshot": Kind.RECORD,
    # search indexes
    "episode_fts": Kind.INDEX,
    "episode_fts_config": Kind.INDEX,
    "episode_fts_data": Kind.INDEX,
    "episode_fts_docsize": Kind.INDEX,
    "episode_fts_idx": Kind.INDEX,
    "belief_fts": Kind.INDEX,
    "belief_fts_config": Kind.INDEX,
    "belief_fts_data": Kind.INDEX,
    "belief_fts_docsize": Kind.INDEX,
    "belief_fts_idx": Kind.INDEX,
    "document_chunk_fts": Kind.INDEX,
    "document_chunk_fts_config": Kind.INDEX,
    "document_chunk_fts_data": Kind.INDEX,
    "document_chunk_fts_docsize": Kind.INDEX,
    "document_chunk_fts_idx": Kind.INDEX,
    # no user content
    "schema_migration": Kind.SYSTEM,
    "identity_core": Kind.SYSTEM,
    "personality_trait": Kind.SYSTEM,
    "personality_history": Kind.SYSTEM,
    "emotion_state": Kind.SYSTEM,
    "relation": Kind.SYSTEM,
    "task_run": Kind.SYSTEM,
    "task_state": Kind.SYSTEM,
    "subscription_cursor": Kind.SYSTEM,
    "reflection_run": Kind.SYSTEM,
    "blob": Kind.SYSTEM,
    "purge_receipt": Kind.SYSTEM,
}


def of(kind: Kind) -> list[str]:
    return [name for name, k in TABLES.items() if k is kind]
