"""Learning from a finished turn (docs/26 §6).

The subscriber to `conversation.turn.completed`. docs/07 §3.1 requires capture to live
here rather than in a graph node — *"capture is a subscriber, which keeps the graph short
and lets capture take its time"* — and that latency allowance is what a model-backed
extractor will need (docs/06 §4.3).

Two things happen when a turn ends:

1. **Capture.** A candidate is built from the exchange and faces the four gates unchanged.
2. **Reinforcement.** The memories that reached the assembled context of a turn that
   actually produced a reply gain the `cited` increment (docs/06 §7.2).

`candidates_from_turn` is rule-based, deliberately. It is the `RulePlanner` of this
milestone: real behaviour, exhaustively testable, no model dependency — and the seam a
model-backed extractor takes over without anything else changing.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Event
from hedwig.core.ports.memory import EpisodeKind
from hedwig.core.types import MemoryId, SalienceInputs, SessionId, TrustTier
from hedwig.memory.capture import CaptureService, EpisodeCandidate, Verdict
from hedwig.memory.salience import reinforcement
from hedwig.memory.store import SqliteMemoryStore

logger = get_logger(__name__)

SUBSCRIPTION = "memory-turn-capture"

REMEMBER_MARKERS = re.compile(
    r"\b(remember|don'?t forget|do not forget|keep in mind|note that|"
    r"for future reference|bear in mind)\b",
    re.IGNORECASE,
)
"""An explicit request to remember is the strongest capture signal there is (docs/06 §4.2),
and the only one this milestone can read without a model."""

LEARNING_STATUSES = frozenset({"completed", "truncated"})
"""A refused or failed turn teaches nothing. Capturing one would store HEDWIG's refusal as
though it were something the user said."""

TITLE_LIMIT = 72


def candidates_from_turn(
    *,
    input_text: str,
    reply: str,
    session_id: str | None = None,
    occurred_at: datetime | None = None,
) -> tuple[EpisodeCandidate, ...]:
    """Build the episodic candidates a turn offers. At most one, today.

    Deliberately unclever. It captures *the exchange*, not the fact inside it — pulling
    "the user's sister is called Ana" out of a conversation is an extraction problem that
    needs a model, and guessing at it with regular expressions would produce confident
    wrong beliefs, which docs/06 §3.2 treats as the failure mode to design against.

    Everything a cognitive engine would contribute — emotional charge, goal relevance,
    entity centrality — stays at zero, because those engines do not exist yet and a
    fabricated signal is worse than an absent one.
    """
    text = input_text.strip()
    if not text:
        return ()

    return (
        EpisodeCandidate(
            title=_title(text),
            content=_content(text, reply.strip()),
            kind=EpisodeKind.INTERACTION,
            session_id=SessionId(session_id) if session_id else None,
            occurred_at=occurred_at,
            salience_inputs=SalienceInputs(user_flagged=bool(REMEMBER_MARKERS.search(text))),
            # The decisive content came from the principal. Only USER content may instruct,
            # and a past user message is still the user speaking (docs/13 §3).
            trust=TrustTier.USER,
            source_ref=f"session:{session_id}" if session_id else None,
        ),
    )


class TurnCaptureSubscriber:
    """Captures and reinforces when a turn completes.

    Idempotent, as every handler must be (docs/04 §2). Redelivery of the *same* event is
    stopped by the bus's `processed_event` ledger; a genuinely repeated exchange is caught
    by the novelty gate, which reinforces the existing memory instead of adding one.
    """

    def __init__(
        self,
        capture: CaptureService,
        store: SqliteMemoryStore,
    ) -> None:
        self._capture = capture
        self._store = store

    async def handle(self, event: Event) -> None:
        payload = event.payload
        status = str(payload.get("status", ""))
        if status not in LEARNING_STATUSES:
            logger.debug("turn taught nothing", extra=fields(status=status))
            return

        reply = str(payload.get("reply", ""))
        await self._reinforce_cited(payload.get("recalled_memory_ids"), had_reply=bool(reply))

        candidates = candidates_from_turn(
            input_text=str(payload.get("input", "")),
            reply=reply,
            session_id=str(payload.get("session_id", "")) or None,
        )
        if not candidates:
            return

        report = await self._capture.capture(episodes=candidates)
        logger.info(
            "turn captured",
            extra=fields(
                turn=payload.get("turn_id"),
                stored=report.count(Verdict.STORED),
                reinforced=report.count(Verdict.REINFORCED),
            ),
        )

    async def _reinforce_cited(self, ids: object, *, had_reply: bool) -> None:
        """Strengthen what was actually shown to the model.

        "Cited" here means *shown*, not *used* — the honest version needs the model to
        declare its citations, which is a later milestone (docs/26 §6.2).
        """
        if not had_reply or not isinstance(ids, Sequence) or isinstance(ids, str):
            return
        memory_ids = [MemoryId(str(value)) for value in ids if value]
        if not memory_ids:
            return
        await self._store.reinforce(memory_ids, reinforcement(cited=True), cause="shown in a reply")


def _title(text: str) -> str:
    """One line, trimmed. The title is for a human scanning a list of memories."""
    first_line = text.splitlines()[0].strip()
    if len(first_line) <= TITLE_LIMIT:
        return first_line
    return first_line[: TITLE_LIMIT - 1].rstrip() + "…"


def _content(text: str, reply: str) -> str:
    """Both sides, verbatim. What was said is the raw material every later extraction and
    consolidation pass works from (docs/05 §7)."""
    if not reply:
        return f"User: {text}"
    return f"User: {text}\nHEDWIG: {reply}"
